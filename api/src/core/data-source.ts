import { DataSource, type DataSourceOptions } from 'typeorm';
import { config } from '../config';
import { ENTITIES } from '../entities';
import { Init1789040000000 } from '../migrations/1789040000000-Init';

export const dataSourceOptions: DataSourceOptions = {
  type: 'postgres',
  url: config.databaseUrl,
  entities: ENTITIES,
  migrations: [Init1789040000000],
  // The schema is owned by the checked-in migrations; the Python worker shares these tables.
  synchronize: false,
  migrationsRun: config.migrationsRun,
  logging: config.dbLogging ? ['query', 'error'] : ['error'],
  poolSize: config.dbPoolSize,
};

/** Used by the TypeORM CLI (`npm run migration:run`). */
export default new DataSource(dataSourceOptions);
