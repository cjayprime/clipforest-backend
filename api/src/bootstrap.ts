import { INestApplication, ValidationPipe } from '@nestjs/common';
import { DocumentBuilder, SwaggerModule } from '@nestjs/swagger';
import { AllExceptionsFilter } from './common/http-exception.filter';

export function configureApp(app: INestApplication) {
  app.setGlobalPrefix('api');
  app.useGlobalPipes(new ValidationPipe({ whitelist: true, forbidNonWhitelisted: true, transform: true }));
  app.useGlobalFilters(new AllExceptionsFilter());
}

export function buildOpenApi(app: INestApplication) {
  const doc = new DocumentBuilder()
    .setTitle('ClipForest API')
    .setDescription(
      'Product API for the ClipForest AI short-form clipping platform. All endpoints require a session cookie ' +
        '(cf_session) or Bearer token unless marked public. Errors use the envelope ' +
        '{ error: { code, message, retryable, correlationId } }.',
    )
    .setVersion('1.0.0')
    .addCookieAuth('cf_session')
    .addBearerAuth()
    .build();
  return SwaggerModule.createDocument(app, doc);
}
