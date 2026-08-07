import type { Page, TestInfo } from '@playwright/test';

type Diagnostics = {
  consoleErrors: string[];
  pageErrors: string[];
  failedResponses: string[];
  failedRequests: string[];
};

export function attachDiagnostics(page: Page, testInfo: TestInfo): Diagnostics {
  const diagnostics: Diagnostics = {
    consoleErrors: [],
    pageErrors: [],
    failedResponses: [],
    failedRequests: [],
  };

  page.on('console', message => {
    if (message.type() === 'error') {
      diagnostics.consoleErrors.push(message.text());
    }
  });

  page.on('pageerror', error => {
    diagnostics.pageErrors.push(error.stack ?? error.message);
  });

  page.on('response', response => {
    if (response.status() >= 500) {
      diagnostics.failedResponses.push(
        `${response.status()} ${response.request().method()} ${response.url()}`
      );
    }
  });

  page.on('requestfailed', request => {
    diagnostics.failedRequests.push(
      `${request.method()} ${request.url()} :: ${request.failure()?.errorText ?? 'unknown'}`
    );
  });

  testInfo.attach('diagnostics', {
    body: Buffer.from(JSON.stringify(diagnostics, null, 2)),
    contentType: 'application/json',
  });

  return diagnostics;
}
