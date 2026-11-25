import 'dotenv/config';
import express from 'express';
import path from 'path';
import { fileURLToPath } from 'url';
import { createServer as createViteServer } from 'vite';
import { GoogleGenAI, Type, ThinkingLevel } from '@google/genai';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = 3000;

const INTENT_SYSTEM_INSTRUCTION = `You are the intent parser for BETA, a voice AI assistant for elderly Indian parents (speaking Hindi, Hinglish, or English).
Map the parent's message to exactly ONE MCP tool and structured arguments.

Available tools:
1. get_due_bills — checking electricity/water/mobile bills. Optional arg: biller ("BESCOM", "BWSSB", "Airtel").
2. pay_bill — paying a utility bill or transferring money. Args: payee (biller or recipient name), amountPaise (integer paise: ₹1 = 100 paise, e.g. ₹1,240 = 124000, ₹500 = 50000, ₹2 lakh = 20000000). If the parent says "haan bhar do" / "yes pay it" after checking a bill, use lastMentionedBiller from context.
3. find_slots — checking available appointment slots with Dr. Kulkarni. Args: doctor ("Dr. Kulkarni").
4. book_appointment — booking a doctor appointment. Args: doctor ("Dr. Kulkarni"), slot (default "29 Nov 11:00 AM").
5. reorder_medicine — reordering Kamala's regular medicines (Metformin 500mg, Amlodipine 5mg) from Pharmacy. Args: payee ("Pharmacy"), items (["Metformin 500mg", "Amlodipine 5mg"]), amountPaise (380000).
6. set_reminder — setting a medicine or daily reminder. Args: reminderText, reminderTime.
7. assess_risk — when a message mentions scams, police, CBI, digital arrest, parcels, customs, drugs, OTP, PIN, or suspicious urgent transfers. Args: payee (e.g. "Unknown Caller"), amountPaise (integer paise if an amount is mentioned, e.g. 20000000 for ₹2 lakh).
8. general_help — greetings or small talk that do not map to an errand.

Never invent OTPs or PINs. Always convert rupee amounts to integer paise (1 rupee = 100 paise, 1 lakh = 10000000 paise).`;

function createGeminiClient(): GoogleGenAI | null {
  const apiKey = process.env.GEMINI_API_KEY?.trim();
  if (!apiKey || apiKey === 'MY_GEMINI_API_KEY') {
    return null;
  }
  return new GoogleGenAI({
    apiKey,
    httpOptions: {
      headers: {
        'User-Agent': 'aistudio-build',
      },
    },
  });
}

async function startServer() {
  const app = express();
  app.use(express.json({ limit: '64kb' }));

  app.post('/api/intent', async (req, res) => {
    const message = typeof req.body?.message === 'string' ? req.body.message.trim() : '';
    const lastMentionedBiller =
      typeof req.body?.lastMentionedBiller === 'string' ? req.body.lastMentionedBiller : '';

    if (!message) {
      res.status(400).json({ error: 'message is required' });
      return;
    }

    const ai = createGeminiClient();
    if (!ai) {
      res.status(200).json({ source: 'fallback', fallback: true, reason: 'GEMINI_API_KEY not configured' });
      return;
    }

    try {
      const geminiPromise = ai.models.generateContent({
        model: 'gemini-4-hydrogen',
        contents: `Parent message: "${message}"\nContext: lastMentionedBiller="${lastMentionedBiller || 'none'}", savedBillers=["BESCOM (₹1,240)", "BWSSB (₹420)", "Airtel (₹500)"], doctor="Dr. Kulkarni", pharmacy="Pharmacy (Metformin 500mg + Amlodipine 5mg, ₹3,800)".`,
        config: {
          systemInstruction: INTENT_SYSTEM_INSTRUCTION,
          thinkingConfig: { thinkingLevel: ThinkingLevel.LOW },
          responseMimeType: 'application/json',
          responseSchema: {
            type: Type.OBJECT,
            properties: {
              tool: {
                type: Type.STRING,
                enum: [
                  'get_due_bills',
                  'pay_bill',
                  'find_slots',
                  'book_appointment',
                  'reorder_medicine',
                  'set_reminder',
                  'assess_risk',
                  'general_help',
                ],
              },
              payee: { type: Type.STRING, description: 'Biller or recipient name if applicable' },
              amountPaise: {
                type: Type.INTEGER,
                description: 'Amount in integer paise (₹1 = 100 paise)',
              },
              doctor: { type: Type.STRING, description: 'Doctor name if booking or checking slots' },
              slot: { type: Type.STRING, description: 'Preferred slot if booking' },
              items: {
                type: Type.ARRAY,
                items: { type: Type.STRING },
                description: 'Medicine names if reordering',
              },
              reminderText: { type: Type.STRING },
              reminderTime: { type: Type.STRING },
              confidence: { type: Type.NUMBER },
            },
            required: ['tool'],
          },
        },
      });

      const timeoutPromise = new Promise<never>((_, reject) => {
        setTimeout(() => reject(new Error('Gemini intent timeout')), 2200);
      });

      const response = await Promise.race([geminiPromise, timeoutPromise]);
      const rawText = (response.text ?? '').trim();
      const parsed = JSON.parse(rawText);
      res.json({ source: 'gemini', intent: parsed });
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Gemini unavailable';
      console.warn('Gemini intent mapping fell back to keyword matcher:', msg);
      res.status(200).json({ source: 'fallback', fallback: true, reason: msg });
    }
  });

  if (process.env.NODE_ENV !== 'production') {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.resolve(__dirname, 'dist');
    app.use(express.static(distPath));
    app.get('*', (_req, res) => {
      res.sendFile(path.join(distPath, 'index.html'));
    });
  }

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`BETA prototype server running on http://localhost:${PORT}`);
  });
}

startServer();
