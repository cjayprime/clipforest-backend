import { Errors } from './errors';

export const CAPTION_PRESETS = ['bold-default', 'karaoke', 'minimal', 'impact'] as const;
export type CaptionPreset = (typeof CAPTION_PRESETS)[number];
export const FRAMING_MODES = ['auto', 'center', 'fit'] as const;
export type FramingMode = (typeof FRAMING_MODES)[number];

/** Immutable settings snapshot stored on each render (PRD §9.1, §11). */
export interface RenderSettings {
  aspectRatio: '9:16';
  framingMode: FramingMode;
  captions: { enabled: boolean; preset: CaptionPreset };
  output: { width: 1080; height: 1920 };
}

export const DEFAULT_RENDER_SETTINGS: RenderSettings = {
  aspectRatio: '9:16',
  framingMode: 'auto',
  captions: { enabled: true, preset: 'bold-default' },
  output: { width: 1080, height: 1920 },
};

export interface RenderSettingsInput {
  aspectRatio?: string;
  framingMode?: string;
  captions?: { enabled?: boolean; preset?: string };
}

export function buildRenderSettings(input: RenderSettingsInput, base: RenderSettings = DEFAULT_RENDER_SETTINGS): RenderSettings {
  const framingMode = (input.framingMode ?? base.framingMode) as FramingMode;
  const preset = (input.captions?.preset ?? base.captions.preset) as CaptionPreset;
  if (!FRAMING_MODES.includes(framingMode)) throw Errors.unsupportedFile(`Unknown framing mode "${framingMode}".`);
  if (!CAPTION_PRESETS.includes(preset)) throw Errors.unsupportedFile(`Unknown caption preset "${preset}".`);
  if (input.aspectRatio && input.aspectRatio !== '9:16') {
    throw Errors.invalidRange('Only 9:16 output is available right now.');
  }
  return {
    aspectRatio: '9:16',
    framingMode,
    captions: { enabled: input.captions?.enabled ?? base.captions.enabled, preset },
    output: { width: 1080, height: 1920 },
  };
}

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
