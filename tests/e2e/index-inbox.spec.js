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

test.describe.serial('Index Inbox browser flows', () => {
  test('first-run owner setup', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#login-title')).toHaveText('Set up Index Inbox');
    await page.locator('#setup-token').fill('playwright-setup-token');
    await page.locator('#username').fill(username);
    await page.locator('#password').fill(password);
    await page.locator('#password-confirmation').fill(password);
    await page.locator('#login-submit').click();
    await expect(page.locator('#app')).toBeVisible();
    await expect(page.locator('.version')).toHaveText('v1.0.0-rc.1');
  });

  test('login and live webhook refresh', async ({ page, request }) => {
    await login(page);
    await webhook(request, 'Note browser capture arrived');
    await expect(page.locator('.capture-notice')).toContainText('Added a standalone note');
    await expect(page.locator('#entries textarea.text')).toHaveValue('browser capture arrived');
  });

  test('group lifecycle and suggestions require confirmation', async ({ page, request }) => {
    await login(page);
    await webhook(request, 'Create Browser forty two');
    await webhook(request, 'Browzer42 needs review');
    await page.locator('#groups-open').click();
    await expect(page.getByRole('button', { name: 'Review suggestions (1)' })).toBeVisible();
    await page.getByRole('button', { name: 'Review suggestions (1)' }).click();
    await expect(page.locator('.suggestion-row')).toContainText('Suggested: BROWSER42');
    await page.locator('.suggestion-row').getByRole('button', { name: 'Accept' }).click();
    await expect(page.getByText('BROWSER42', { exact: false })).toBeVisible();

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
    await page.locator('#groups-open').click();
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
    await expect(page.locator('#capture')).toBeVisible();
    await page.locator('#groups-open').click();
    await expect(page.locator('#info-dialog')).toBeVisible();
    await expect(page.locator('#info-dialog')).toHaveCSS('width', '390px');
    await expect(page.locator('.group-row').filter({ hasText: 'BROWSER43' }).getByRole('button', { name: 'Timeline' })).toBeVisible();
  });

  test('mobile header and storage actions do not overlap', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page);
    await expect(page.locator('header')).toHaveCSS('min-height', '64px');
    await page.locator('#status-open').click();
    await expect(page.locator('.storage-status')).toBeVisible();
    const buttons = page.locator('.storage-status .modal-actions button');
    await expect(buttons).toHaveCount(8);
    const boxes = await buttons.evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect()).map(({ top, bottom, left, right }) => ({ top, bottom, left, right })));
    for (let i = 1; i < boxes.length; i += 1) expect(boxes[i].top).toBeGreaterThanOrEqual(boxes[i - 1].bottom);
  });
});
