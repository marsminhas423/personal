// Style presets. Video models respond to camera/lens/lighting language far more reliably
// than to adjectives like "cinematic", so each chip appends a concrete production clause.

export const STYLE_PRESETS = [
  { id: 'cinematic', label: 'Cinematic', clause: 'anamorphic lens, shallow depth of field, filmic contrast, 24fps motion blur' },
  { id: 'documentary', label: 'Documentary', clause: 'handheld camera, natural available light, subtle lens breathing' },
  { id: 'drone', label: 'Drone', clause: 'aerial drone shot, slow forward push, wide establishing framing' },
  { id: 'macro', label: 'Macro', clause: 'extreme macro lens, razor-thin focal plane, slow dolly in' },
  { id: 'goldenhour', label: 'Golden hour', clause: 'golden hour backlight, long soft shadows, warm haze' },
  { id: 'noir', label: 'Noir', clause: 'high-contrast low-key lighting, hard shadows, desaturated palette' },
  { id: 'neon', label: 'Neon', clause: 'neon practical lights, wet reflective surfaces, cyan and magenta colour cast' },
  { id: 'analog', label: 'Analog film', clause: 'shot on 35mm film, visible grain, slight halation on highlights' },
  { id: 'anime', label: 'Anime', clause: 'hand-drawn 2D anime style, cel shading, expressive keyframes' },
  { id: 'claymation', label: 'Claymation', clause: 'stop-motion claymation, tactile fingerprints, 12fps stepped motion' },
  { id: 'timelapse', label: 'Timelapse', clause: 'timelapse, static locked-off tripod, fast-moving clouds and light' },
  { id: 'slowmo', label: 'Slow motion', clause: 'high-speed slow motion at 240fps, fluid detailed movement' },
  { id: 'orbit', label: 'Orbit', clause: 'smooth 180-degree orbital camera move around the subject' },
  { id: 'product', label: 'Product', clause: 'clean studio seamless background, soft key with rim light, slow turntable rotation' },
];

const byId = new Map(STYLE_PRESETS.map((p) => [p.id, p]));

/** Fold the selected chips into a single prompt string. */
export function composePrompt(base, styleIds = []) {
  const text = (base ?? '').trim();
  const clauses = styleIds.map((id) => byId.get(id)?.clause).filter(Boolean);
  if (!clauses.length) return text;
  const joined = clauses.join(', ');
  return text ? `${text.replace(/[.\s]+$/, '')}. ${joined}.` : `${joined}.`;
}

export const styleLabel = (id) => byId.get(id)?.label ?? id;
