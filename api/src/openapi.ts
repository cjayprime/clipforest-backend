/**
 * Generates backend/contracts/openapi.json without connecting to Postgres,
 * Redis or storage:  npm run build && npm run openapi
 */
import 'reflect-metadata';
import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

process.env.OPENAPI_ONLY = '1';

async function main() {
  // Loaded after OPENAPI_ONLY is set so services skip their connections.
  const { NestFactory } = await import('@nestjs/core');
  const { AppModule } = await import('./app.module');
  const { buildOpenApi, configureApp } = await import('./bootstrap');
  const app = await NestFactory.create(AppModule, { logger: false });
  configureApp(app);
  await app.init();
  const out = resolve(__dirname, '../../contracts/openapi.json');
  writeFileSync(out, JSON.stringify(buildOpenApi(app), null, 2) + '\n');
  await app.close();
  console.log(`Wrote ${out}`);
}

main().catch((err: unknown) => {
  console.error(err);
  process.exit(1);
});
