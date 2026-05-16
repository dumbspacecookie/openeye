/**
 * FrameSampler — decides which frames are worth sending to a vision model.
 *
 * A factory camera at 30fps produces 1,800 frames a minute. Logging every
 * one is wasteful (most are duplicates of the previous one) and floods the
 * agent's context. This helper gives you two complementary strategies:
 *
 *   1. Every-Nth-frame:    log a frame every N captures, regardless of content
 *   2. Scene-change:       log when the average pixel intensity moves enough
 *                          to suggest something visible changed
 *
 * Use both together. The Nth-frame rule guarantees a baseline cadence
 * (the camera is alive), and the scene-change rule catches the
 * step-completed-just-now moment between baselines.
 *
 * The intensity check is intentionally crude — it's a coarse-grained
 * "did anything change?" filter, not a vision model. For procedure
 * verification you want most decisions made by the actual vision/LLM
 * pipeline. This is just there to save you 95%+ of the API calls.
 */

export interface FrameSamplerOptions {
  /** Log every Nth frame regardless of content. Default 30 (≈1/sec at 30fps). */
  everyN?: number;
  /**
   * Average-intensity-diff threshold for "scene changed" in [0, 1].
   * 0 logs every frame, 1 logs nothing on diff alone. Default 0.04.
   * Tune up if your scene has natural flicker (lights, conveyor belts).
   */
  sceneChangeThreshold?: number;
  /** Disable scene-change detection — only every-Nth. Default false. */
  disableSceneChange?: boolean;
  /**
   * Cooldown after a scene-change trigger, in frames. Prevents a single
   * motion event from triggering on every frame for the next half-second.
   * Default 5.
   */
  cooldownFrames?: number;
}

export interface FrameDecision {
  /** Whether to send this frame to the vision model + log it */
  should_describe: boolean;
  /** Why — useful for debugging your sampling rates */
  reason: "every_nth" | "scene_change" | "skipped_interval" | "skipped_cooldown" | "skipped_similar";
  /** Pixel-intensity diff from the previous frame, in [0, 1]. null if no prior frame. */
  intensity_diff: number | null;
  /** Monotonic frame counter. */
  frame_number: number;
}

/**
 * Compute the mean intensity of a JPEG/PNG buffer by sampling its raw bytes.
 *
 * IMPORTANT: This does NOT decode the image — it averages compressed bytes.
 * That's intentional: full decode for every frame would cost more than the
 * vision-model call we're trying to avoid. The signal is noisy but useful:
 * two JPEGs of the same static scene have similar mean-byte values; a
 * meaningful scene change shifts entropy enough to move the mean.
 *
 * If your encoder is unusual or you need real visual diffs, swap this for
 * a proper decode (e.g. sharp.metadata or jimp) and a histogram.
 */
function meanByteValue(buffer: Buffer | Uint8Array): number {
  // Skip the file header so we sample compressed image data, not metadata.
  // 64-byte skip handles JPEG SOI + most APP markers, PNG signature + IHDR.
  const start = Math.min(64, buffer.length);
  if (start >= buffer.length) return 0;
  let sum = 0;
  let count = 0;
  // Stride through ~1024 bytes to keep this O(1) regardless of frame size
  const stride = Math.max(1, Math.floor((buffer.length - start) / 1024));
  for (let i = start; i < buffer.length; i += stride) {
    sum += buffer[i];
    count++;
  }
  return count === 0 ? 0 : sum / count / 255; // normalized [0, 1]
}

export class FrameSampler {
  private opts: Required<FrameSamplerOptions>;
  private frameNumber = 0;
  private framesSinceLastTrigger = Infinity;
  private cooldownRemaining = 0;
  private lastIntensity: number | null = null;

  constructor(opts: FrameSamplerOptions = {}) {
    this.opts = {
      everyN: opts.everyN ?? 30,
      sceneChangeThreshold: opts.sceneChangeThreshold ?? 0.04,
      disableSceneChange: opts.disableSceneChange ?? false,
      cooldownFrames: opts.cooldownFrames ?? 5,
    };
  }

  /**
   * Feed one frame. Returns a decision telling you whether to invoke the
   * vision model. Increments internal counters either way.
   */
  consider(frameBuffer: Buffer | Uint8Array): FrameDecision {
    this.frameNumber++;
    this.framesSinceLastTrigger++;
    if (this.cooldownRemaining > 0) this.cooldownRemaining--;

    const currentIntensity = meanByteValue(frameBuffer);
    const diff =
      this.lastIntensity === null
        ? null
        : Math.abs(currentIntensity - this.lastIntensity);
    this.lastIntensity = currentIntensity;

    // Cadence rule first — every Nth frame always triggers
    if (this.frameNumber % this.opts.everyN === 0) {
      this.framesSinceLastTrigger = 0;
      this.cooldownRemaining = this.opts.cooldownFrames;
      return {
        should_describe: true,
        reason: "every_nth",
        intensity_diff: diff,
        frame_number: this.frameNumber,
      };
    }

    // Scene-change rule
    if (
      !this.opts.disableSceneChange &&
      diff !== null &&
      diff >= this.opts.sceneChangeThreshold &&
      this.cooldownRemaining === 0
    ) {
      this.framesSinceLastTrigger = 0;
      this.cooldownRemaining = this.opts.cooldownFrames;
      return {
        should_describe: true,
        reason: "scene_change",
        intensity_diff: diff,
        frame_number: this.frameNumber,
      };
    }

    return {
      should_describe: false,
      reason:
        this.cooldownRemaining > 0
          ? "skipped_cooldown"
          : diff !== null && diff < this.opts.sceneChangeThreshold
            ? "skipped_similar"
            : "skipped_interval",
      intensity_diff: diff,
      frame_number: this.frameNumber,
    };
  }

  /** Reset the sampler — useful at the start of a new visual session. */
  reset(): void {
    this.frameNumber = 0;
    this.framesSinceLastTrigger = Infinity;
    this.cooldownRemaining = 0;
    this.lastIntensity = null;
  }

  /** Current frame number, useful as `sequenceNum` in logFrame(). */
  getFrameNumber(): number {
    return this.frameNumber;
  }
}
