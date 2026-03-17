import { describe, expect, it } from 'vitest';
import request from 'supertest';
import { createApp } from '../src/server.js';

describe('phase1-scanner server', () => {
  it('returns health status', async () => {
    const app = createApp();
    const res = await request(app).get('/health');

    expect(res.status).toBe(200);
    expect(res.body.status).toBe('ok');
  });

  it('validates request body', async () => {
    const app = createApp();
    const res = await request(app).post('/scan').send({});

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/url/i);
  });
});
