export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!apiKey) {
    return res.status(500).json({
      error: 'OPENROUTER_API_KEY is not configured in Vercel Environment Variables.',
    });
  }

  const { prompt } = req.body || {};
  if (!prompt || typeof prompt !== 'string' || prompt.length > 30000) {
    return res.status(400).json({ error: 'Invalid or missing prompt.' });
  }

  const freeModels = [
    'google/gemma-4-31b-it:free',
    'google/gemma-4-26b-a4b-it:free',
    'openrouter/free',
  ];

  let lastError = 'No free models responded.';
  for (const model of freeModels) {
    try {
      const resp = await fetch('https://openrouter.ai/api/v1/chat/completions', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${apiKey}`,
          'Content-Type': 'application/json',
          'HTTP-Referer': 'https://trade.btcbri.com',
          'X-Title': 'BTCBRI Trading Desk',
        },
        body: JSON.stringify({
          model,
          messages: [
            {
              role: 'system',
              content:
                'Ти — кількісний аналітик BTC на горизонті 1–3 роки. Відповідай українською мовою, чітко, структуровано, без води, спираючись виключно на передані дані BTCBRI.',
            },
            { role: 'user', content: prompt },
          ],
          max_tokens: 1800,
          temperature: 0.3,
        }),
      });

      const data = await resp.json();
      if (!resp.ok) {
        lastError = data?.error?.message || `HTTP ${resp.status}`;
        continue;
      }

      const content = data?.choices?.[0]?.message?.content;
      if (content && content.trim()) {
        return res.status(200).json({
          report: content.trim(),
          model: data?.model || model,
        });
      }
      lastError = `Empty response from ${model}`;
    } catch (err) {
      lastError = err.message || 'Network error';
    }
  }

  return res.status(502).json({ error: `OpenRouter free models unavailable: ${lastError}` });
}
