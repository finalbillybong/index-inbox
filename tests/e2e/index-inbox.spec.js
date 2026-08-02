const { test, expect } = require('@playwright/test');

const username = 'browser-owner';
const password = 'browser owner password';

async function login(page) {
  await page.goto('/');
  await page.locator('#username').fill(username);
  await page.locator('#password').fill(password);
  await page.locator('#login-submit').click();
  await expect(page.locator('#app')).toBeVisible();
}

async function webhook(request, transcription) {
  const response = await request.post('/webhook/index', {
    headers: { 'X-Webhook-Secret': 'playwright-webhook-secret' },
    data: { transcription }
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function openMenu(page, target) {
  await page.locator('#menu-toggle').click();
  await page.locator(target).click();
}

test.describe('Index Inbox browser flows', () => {
  // These tests intentionally build on one server/database lifecycle. Retrying a
  // serial group replays owner setup against an already-initialized database.
  test.describe.configure({ mode: 'serial', retries: 0 });

  test('first-run owner setup', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#login-title')).toHaveText('Set up Index Inbox');
    await page.locator('#setup-token').fill('playwright-setup-token');
    await page.locator('#username').fill(username);
    await page.locator('#password').fill(password);
    await page.locator('#password-confirmation').fill(password);
    await page.locator('#login-submit').click();
    await expect(page.locator('#app')).toBeVisible();
    await expect(page.locator('.version')).toHaveText('v1.2.0');
  });

  test('login and live webhook refresh', async ({ page, request }) => {
    const livePoll = page.waitForRequest(candidate => candidate.url().includes('/api/changes?since='));
    await login(page);
    await livePoll;
    await webhook(request, 'Note browser capture arrived');
    await expect(page.locator('.capture-notice')).toContainText('Added a standalone note');
    await expect(page.locator('#entries textarea.text')).toHaveValue('browser capture arrived');
  });

  test('natural-language reminder and early alert can be completed', async ({ page, request }) => {
    await login(page);
    await webhook(request, 'Remind me tomorrow at 3pm to test the browser reminder with one hour notice');
    await page.locator('#state').selectOption('reminders');
    const card=page.locator('.card').filter({has:page.locator('.reminder-completed:not(:disabled)')}).first();
    await expect(card).toBeVisible();
    await expect(card.locator('textarea.text')).toHaveValue('test the browser reminder');
    await expect(card.locator('.due-at')).not.toHaveValue('');
    await expect(card.locator('.notify-before')).toHaveValue('60');
    await card.locator('.reminder-completed').check();
    await expect(card).toBeHidden();
  });

  test('authenticated web app downloads the embedded Android release', async ({ page }) => {
    await login(page);
    const button = page.locator('#android-install');
    await expect(button).toHaveText('Android 0.10.1');
    const downloadPromise = page.waitForEvent('download');
    await button.click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe('index-inbox.apk');
  });

  test('Index Ring integration reveals the webhook secret after password confirmation', async ({ page }) => {
    await login(page);
    await openMenu(page, '#integrations-open');
    await expect(page.locator('.index-ring-integration')).toContainText('X-Webhook-Secret');
    await expect(page.locator('#index-ring-url')).toHaveValue('http://127.0.0.1:5055/webhook/index');
    await page.getByRole('button', { name: 'Reveal' }).click();
    await page.locator('#confirm-input').fill(password);
    await page.locator('#confirm-ok').click();
    await expect(page.locator('#index-ring-secret')).toHaveValue('playwright-webhook-secret');
    await page.getByRole('button', { name: 'Send test capture' }).click();
    await expect(page.locator('#info')).toContainText('Test capture added to the inbox.');
  });

  test('group lifecycle and suggestions require confirmation', async ({ page, request }) => {
    await login(page);
    await webhook(request, 'Create Browser forty two');
    await webhook(request, 'Browzer42 needs review');
    await openMenu(page, '#groups-open');
    await expect(page.getByRole('button', { name: 'Review suggestions (1)' })).toBeVisible();
    await page.getByRole('button', { name: 'Review suggestions (1)' }).click();
    await expect(page.locator('.suggestion-row')).toContainText('Suggested: BROWSER42');
    await page.locator('.suggestion-row').getByRole('button', { name: 'Accept' }).click();
    await expect(page.locator('.group-row').filter({ hasText: 'BROWSER42' })).toBeVisible();

    const row = page.locator('.group-row').filter({ hasText: 'BROWSER42' });
    await row.getByRole('button', { name: 'Rename' }).click();
    await page.locator('#confirm-input').fill('BROWSER43');
    await page.locator('#confirm-ok').click();
    await expect(page.locator('.group-row').filter({ hasText: 'BROWSER43' })).toBeVisible();
    const renamed = page.locator('.group-row').filter({ hasText: 'BROWSER43' });
    await renamed.getByRole('button', { name: 'Archive' }).click();
    await expect(page.locator('.group-row').filter({ hasText: 'BROWSER43 · archived' })).toBeVisible();
    await page.locator('.group-row').filter({ hasText: 'BROWSER43' }).getByRole('button', { name: 'Reopen' }).click();
    await expect(page.locator('.group-row').filter({ hasText: 'BROWSER43' })).not.toContainText('archived');
  });

  test('timeline saves to inbox and group export downloads', async ({ page }) => {
    await login(page);
    await openMenu(page, '#groups-open');
    const row = page.locator('.group-row').filter({ hasText: 'BROWSER43' });
    await row.getByRole('button', { name: 'Timeline' }).click();
    const transcription = page.locator('.timeline-entry textarea').filter({ hasValue: 'needs review' });
    await transcription.fill('review completed in browser');
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Markdown' }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe('index-inbox-browser43.md');
    await page.getByRole('button', { name: 'Save & Back' }).click();
    await expect(page.locator('.group-row').filter({ hasText: 'BROWSER43' })).toBeVisible();
    await page.locator('#info-dialog .close').click();
    await expect.poll(() => page.locator('#entries textarea.text').evaluateAll(nodes => nodes.map(node => node.value))).toContain('review completed in browser');
  });

  test('mobile controls and group dialog remain usable', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page);
    await expect(page.locator('#nav-capture')).toBeVisible();
    await openMenu(page, '#groups-open');
    await expect(page.locator('#info-dialog')).toBeVisible();
    await expect(page.locator('#info-dialog')).toHaveCSS('width', '390px');
    await expect(page.locator('.group-row').filter({ hasText: 'BROWSER43' }).getByRole('button', { name: 'Timeline' })).toBeVisible();
  });

  test('Android mobile header offers only the APK action', async ({ page }) => {
    await page.addInitScript(() => Object.defineProperty(navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 (Linux; Android 16; Pixel 9) AppleWebKit/537.36 Chrome/140 Mobile Safari/537.36',
    }));
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page);
    await expect(page.locator('#android-install')).toHaveText('↓ Android APK');
    await expect(page.locator('#android-install')).toBeVisible();
    await expect(page.locator('#install')).toBeHidden();
  });

  test('iOS mobile header offers PWA instructions instead of the APK', async ({ page }) => {
    await page.addInitScript(() => Object.defineProperty(navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1',
    }));
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page);
    await expect(page.locator('#android-install')).toBeHidden();
    await expect(page.locator('#install')).toHaveText('Install PWA');
    await page.locator('#install').click();
    await expect(page.locator('#info')).toContainText('Add to Home Screen');
  });

  test('browser audio recording is previewed and transcribed', async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) }
      });
      class FakeMediaRecorder {
        static isTypeSupported() { return true; }
        constructor(stream, options = {}) { this.stream = stream; this.mimeType = options.mimeType || 'audio/webm'; this.state = 'inactive'; }
        start() { this.state = 'recording'; }
        stop() {
          this.state = 'inactive';
          this.ondataavailable?.({ data: new Blob(['recorded-audio'], { type: this.mimeType }) });
          this.onstop?.();
        }
      }
      Object.defineProperty(window, 'MediaRecorder', { configurable: true, value: FakeMediaRecorder });
    });
    await page.route('**/api/transcribe', route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ok: true, transcription: 'locally transcribed browser audio', language: 'en', duration: 1 })
    }));
    await login(page);
    await page.locator('#capture').click();
    await page.locator('#record').click();
    await expect(page.locator('#record-status')).toContainText('Recording');
    await page.locator('#record').click();
    await expect(page.locator('#record-preview')).toBeVisible();
    await expect(page.locator('#manual-text')).toHaveValue('locally transcribed browser audio');
    await expect(page.locator('#record-status')).toContainText('Transcription ready');
  });

  test('mobile header and storage actions do not overlap', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page);
    await expect(page.locator('header')).toHaveCSS('min-height', '64px');
    await openMenu(page, '#status-open');
    await expect(page.locator('.storage-status')).toBeVisible();
    const buttons = page.locator('.storage-status .modal-actions button');
    await expect(buttons).toHaveCount(8);
    const boxes = await buttons.evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect()).map(({ top, bottom, left, right }) => ({ top, bottom, left, right })));
    for (let i = 1; i < boxes.length; i += 1) expect(boxes[i].top).toBeGreaterThanOrEqual(boxes[i - 1].bottom);
  });

  test('mobile Back closes capture before leaving the inbox', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page);
    await page.locator('#nav-capture').click();
    await expect(page.locator('#capture-dialog')).toBeVisible();
    await page.goBack();
    await expect(page.locator('#capture-dialog')).toBeHidden();
    await expect(page.locator('#app')).toBeVisible();
  });
});
