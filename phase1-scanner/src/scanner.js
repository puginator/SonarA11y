import { chromium } from 'playwright';
import AxeBuilder from '@axe-core/playwright';
import { validateAxePayload } from './schemaValidator.js';

function withTimeout(promise, timeoutMs, label) {
  let timeoutId;
  const timeoutPromise = new Promise((_, reject) => {
    timeoutId = setTimeout(() => reject(new Error(`${label} timed out after ${timeoutMs}ms`)), timeoutMs);
  });
  return Promise.race([promise, timeoutPromise]).finally(() => clearTimeout(timeoutId));
}

async function captureNodeContext(page, selector, failureSummary, maxScreenshotBytes) {
  const node = {
    targetSelector: selector,
    rawHtml: '',
    failureSummary
  };

  if (!selector || typeof selector !== 'string') {
    node.warning = 'No selector returned by axe for this node.';
    return node;
  }

  try {
    const element = page.locator(selector).first();
    const count = await element.count();
    if (count === 0) {
      node.warning = 'Selector did not resolve to an element on page.';
      return node;
    }

    node.rawHtml = await element.evaluate((el) => el.outerHTML || '');

    const image = await element.screenshot({ type: 'png', scale: 'css' });
    if (image.length > maxScreenshotBytes) {
      node.warning = `Element screenshot exceeded ${maxScreenshotBytes} bytes and was omitted.`;
      return node;
    }

    node.elementScreenshotBase64 = image.toString('base64');
    return node;
  } catch (error) {
    node.warning = `Failed to capture full node context: ${error.message}`;
    return node;
  }
}

export async function scanUrl({ url, viewport = { width: 1920, height: 1080 }, config }) {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();

  try {
    await page.goto(url, {
      waitUntil: 'networkidle',
      timeout: config.navigationTimeoutMs
    });

    const axeBuilder = new AxeBuilder({ page }).withTags(config.axeTags);

    const results = await withTimeout(
      axeBuilder.analyze(),
      config.scanTimeoutMs,
      'Axe accessibility scan'
    );

    const violations = [];
    for (const violation of results.violations) {
      const nodes = [];
      for (const failingNode of violation.nodes) {
        const selector = Array.isArray(failingNode.target) ? failingNode.target[0] : undefined;
        const failureSummary = failingNode.failureSummary || 'No failure summary provided by axe.';
        const nodeContext = await captureNodeContext(page, selector, failureSummary, config.maxScreenshotBytes);
        nodes.push(nodeContext);
      }

      violations.push({
        ruleId: violation.id,
        impact: violation.impact || 'moderate',
        description: violation.description,
        nodes
      });
    }

    const payload = {
      scanMetadata: {
        url,
        timestamp: new Date().toISOString(),
        viewport: `${viewport.width}x${viewport.height}`
      },
      violations
    };

    validateAxePayload(payload);
    return payload;
  } finally {
    await context.close();
    await browser.close();
  }
}
