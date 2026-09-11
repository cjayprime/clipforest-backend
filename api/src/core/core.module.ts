import { Global, Module, type DynamicModule } from '@nestjs/common';
import { getRepositoryToken, TypeOrmModule } from '@nestjs/typeorm';
import { DataSource } from 'typeorm';
import { config } from '../config';
import { ENTITIES } from '../entities';
import { dataSourceOptions } from './data-source';
import { EventsService } from './events.service';
import { MetricsService } from './metrics.service';
import { QueueService } from './queue.service';
import { StorageService } from './storage.service';

/**
 * Database wiring. `npm run openapi` boots the app without any infrastructure,
 * so in that mode repository tokens are stubbed instead of connecting.
 */
function databaseImports(): DynamicModule[] {
  if (config.openapiOnly) return [];
  return [TypeOrmModule.forRoot(dataSourceOptions) as DynamicModule, TypeOrmModule.forFeature(ENTITIES) as DynamicModule];
}

const stubProviders = config.openapiOnly
  ? [...ENTITIES.map((e) => ({ provide: getRepositoryToken(e), useValue: {} })), { provide: DataSource, useValue: {} }]
  : [];

@Global()
@Module({
  imports: databaseImports(),
  providers: [StorageService, QueueService, EventsService, MetricsService, ...stubProviders],
  exports: [
    StorageService,
    QueueService,
    EventsService,
    MetricsService,
    ...(config.openapiOnly ? stubProviders.map((p) => p.provide) : [TypeOrmModule]),
  ],
})
export class CoreModule {}
