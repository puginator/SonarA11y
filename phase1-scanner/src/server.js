import express from 'express';
import { config } from './config.js';
import { scanUrl } from './scanner.js';

export function createApp() {
  const app = express();
  app.use(express.json({ limit: '2mb' }));

  app.get('/health', (_req, res) => {
    res.json({ status: 'ok', service: 'phase1-scanner' });
  });

  app.post('/scan', async (req, res) => {
    const { url, viewport } = req.body || {};

    if (!url || typeof url !== 'string') {
      return res.status(400).json({ error: 'Request body must include a valid `url`.' });
    }

    try {
      const normalizedViewport = viewport && viewport.width && viewport.height
        ? { width: Number(viewport.width), height: Number(viewport.height) }
        : { width: 1920, height: 1080 };

      const payload = await scanUrl({ url, viewport: normalizedViewport, config });
      return res.json(payload);
    } catch (error) {
      return res.status(500).json({
        error: 'Scan failed',
        detail: error.message
      });
    }
  });

  return app;
}

if (process.env.NODE_ENV !== 'test') {
  const app = createApp();
  app.listen(config.port, () => {
    console.log(`phase1-scanner listening on ${config.port}`);
  });
}
