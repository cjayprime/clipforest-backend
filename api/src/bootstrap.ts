import { INestApplication, ValidationPipe } from '@nestjs/common';
import { DocumentBuilder, SwaggerModule } from '@nestjs/swagger';
import { AllExceptionsFilter } from './common/http-exception.filter';

/** Everything that must be true of the app whether it is served or introspected. */
export function configureApp(app: INestApplication) {
  app.setGlobalPrefix('api');
  app.useGlobalPipes(new ValidationPipe({ whitelist: true, forbidNonWhitelisted: true, transform: true }));
  app.useGlobalFilters(new AllExceptionsFilter());
}

/** The published contract in backend/contracts/openapi.json. */
export function buildOpenApi(app: INestApplication) {
  const doc = new DocumentBuilder()
    .setTitle('ClipRover API')
    .setDescription(
      'Product API for the ClipRover AI short-form clipping platform. All endpoints require a session cookie ' +
        '(cr_session) or Bearer token unless marked public. Errors use the envelope ' +
        '{ error: { code, message, retryable, correlationId } }.',
    )
    .setVersion('1.0.0')
    .addCookieAuth('cr_session')
    .addBearerAuth()
    .build();
  return SwaggerModule.createDocument(app, doc);
}
