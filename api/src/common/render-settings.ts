import { Errors } from './errors';

/** Caption styles the renderer can burn in. Shared with the worker's ASS builder. */
export const CAPTION_PRESETS = ['bold-default', 'karaoke', 'minimal', 'impact'] as const;
export type CaptionPreset = (typeof CAPTION_PRESETS)[number];

/**
 * How the source becomes the output frame: follow the subject, crop the centre,
 * or fit the whole frame over a blurred background.
 */
export const FRAMING_MODES = ['auto', 'center', 'fit'] as const;
export type FramingMode = (typeof FRAMING_MODES)[number];

/** Output frame size in pixels for each supported aspect ratio. */
export const ASPECT_RATIOS = {
  '9:16': { width: 1080, height: 1920 },
  '4:5': { width: 1080, height: 1350 },
  '1:1': { width: 1080, height: 1080 },
  '16:9': { width: 1920, height: 1080 },
} as const;
export type AspectRatio = keyof typeof ASPECT_RATIOS;
export const ASPECT_RATIO_KEYS = Object.keys(ASPECT_RATIOS) as AspectRatio[];

/** Immutable settings snapshot stored on each render (PRD §9.1, §11). */
export interface RenderSettings {
  aspectRatio: AspectRatio;
  framingMode: FramingMode;
  captions: { enabled: boolean; preset: CaptionPreset };
  output: { width: number; height: number };
}

export const DEFAULT_RENDER_SETTINGS: RenderSettings = {
  aspectRatio: '9:16',
  framingMode: 'auto',
  captions: { enabled: true, preset: 'bold-default' },
  output: { ...ASPECT_RATIOS['9:16'] },
};

/** Whatever the client sent — every field optional and untrusted. */
export interface RenderSettingsInput {
  aspectRatio?: string;
  framingMode?: string;
  captions?: { enabled?: boolean; preset?: string };
}

/**
 * Resolves client input against a base (the parent render's settings, or the
 * defaults) into the exact snapshot that will be stored and hashed.
 */
export function buildRenderSettings(
  input: RenderSettingsInput,
  base: RenderSettings = DEFAULT_RENDER_SETTINGS,
): RenderSettings {
  const framingMode = (input.framingMode ?? base.framingMode) as FramingMode;
  const preset = (input.captions?.preset ?? base.captions.preset) as CaptionPreset;
  const aspectRatio = (input.aspectRatio ?? base.aspectRatio) as AspectRatio;
  if (!FRAMING_MODES.includes(framingMode)) throw Errors.unsupportedFile(`Unknown framing mode "${framingMode}".`);
  if (!CAPTION_PRESETS.includes(preset)) throw Errors.unsupportedFile(`Unknown caption preset "${preset}".`);
  if (!ASPECT_RATIO_KEYS.includes(aspectRatio)) throw Errors.invalidRange(`Unknown aspect ratio "${aspectRatio}".`);
  return {
    aspectRatio,
    framingMode,
    captions: { enabled: input.captions?.enabled ?? base.captions.enabled, preset },
    output: { ...ASPECT_RATIOS[aspectRatio] },
  };
}

/**
 * A clip range must be whole milliseconds, inside the source, and within the
 * configured clip length bounds. Checked here rather than in a DTO because the
 * limits depend on the video being rendered.
 */
export function validateRenderRange(
  startMs: number,
  endMs: number,
  videoDurationMs: number,
  minMs: number,
  maxMs: number,
): void {
  if (!Number.isInteger(startMs) || !Number.isInteger(endMs)) throw Errors.invalidRange('Start and end must be whole milliseconds.');
  if (startMs < 0) throw Errors.invalidRange('Start must be at or after the beginning of the video.');
  if (endMs <= startMs) throw Errors.invalidRange('End must be after start.');
  if (endMs > videoDurationMs) throw Errors.invalidRange('End must be within the video.');
  const d = endMs - startMs;
  if (d < minMs) throw Errors.invalidRange(`Clips must be at least ${Math.round(minMs / 1000)} seconds long.`);
  if (d > maxMs) throw Errors.invalidRange(`Clips can be at most ${Math.round(maxMs / 1000)} seconds long.`);
}
