import { describe, it, expect, beforeEach } from "vitest";
import { FrameSampler } from "../src/frame-sampler.js";

function uniformFrame(byteValue: number, size = 4096): Buffer {
  return Buffer.alloc(size, byteValue);
}

describe("FrameSampler — every-Nth-frame rule", () => {
  it("triggers exactly every N frames when scene is static", () => {
    const sampler = new FrameSampler({ everyN: 10, disableSceneChange: true });
    const buf = uniformFrame(128);
    const triggers: number[] = [];
    for (let i = 0; i < 25; i++) {
      const d = sampler.consider(buf);
      if (d.should_describe) triggers.push(d.frame_number);
    }
    expect(triggers).toEqual([10, 20]);
  });

  it("first frame is not described unless N=1", () => {
    const sampler = new FrameSampler({ everyN: 30, disableSceneChange: true });
    const d = sampler.consider(uniformFrame(100));
    expect(d.should_describe).toBe(false);
    expect(d.frame_number).toBe(1);
  });

  it("reason is correct on every-Nth trigger", () => {
    const sampler = new FrameSampler({ everyN: 3, disableSceneChange: true });
    const buf = uniformFrame(100);
    sampler.consider(buf); // 1
    sampler.consider(buf); // 2
    const d = sampler.consider(buf); // 3
    expect(d.should_describe).toBe(true);
    expect(d.reason).toBe("every_nth");
  });
});

describe("FrameSampler — scene-change rule", () => {
  it("triggers on a large intensity jump", () => {
    const sampler = new FrameSampler({
      everyN: 1000,                 // effectively disable Nth rule
      sceneChangeThreshold: 0.1,
      cooldownFrames: 0,
    });
    sampler.consider(uniformFrame(50)); // establish baseline
    const d = sampler.consider(uniformFrame(200)); // big shift
    expect(d.should_describe).toBe(true);
    expect(d.reason).toBe("scene_change");
    expect(d.intensity_diff).toBeGreaterThan(0.1);
  });

  it("does not trigger on tiny changes below threshold", () => {
    const sampler = new FrameSampler({
      everyN: 1000,
      sceneChangeThreshold: 0.1,
      cooldownFrames: 0,
    });
    sampler.consider(uniformFrame(100));
    const d = sampler.consider(uniformFrame(102));
    expect(d.should_describe).toBe(false);
    expect(d.reason).toBe("skipped_similar");
  });

  it("respects cooldown — does not re-trigger right after a scene change", () => {
    const sampler = new FrameSampler({
      everyN: 1000,
      sceneChangeThreshold: 0.1,
      cooldownFrames: 3,
    });
    sampler.consider(uniformFrame(50));
    // First scene change should trigger
    const first = sampler.consider(uniformFrame(200));
    expect(first.should_describe).toBe(true);
    // Next two frames have big diffs too but cooldown is active
    const blocked1 = sampler.consider(uniformFrame(50));
    const blocked2 = sampler.consider(uniformFrame(200));
    expect(blocked1.should_describe).toBe(false);
    expect(blocked2.should_describe).toBe(false);
    expect(blocked2.reason).toBe("skipped_cooldown");
  });
});

describe("FrameSampler — combined rules", () => {
  it("counts triggers from both rules separately", () => {
    const sampler = new FrameSampler({
      everyN: 5,
      sceneChangeThreshold: 0.2,
      cooldownFrames: 0,
    });
    // 20 frames, mostly static, one big shift at frame 8
    const triggers: string[] = [];
    for (let i = 0; i < 20; i++) {
      const intensity = i === 8 ? 250 : 50;
      const d = sampler.consider(uniformFrame(intensity));
      if (d.should_describe) triggers.push(`${d.frame_number}:${d.reason}`);
    }
    // Expect every-5th hits: 5, 10, 15, 20 + scene_change at frame 9 (i=8)
    expect(triggers).toContain("5:every_nth");
    expect(triggers).toContain("9:scene_change");
    expect(triggers).toContain("10:every_nth");
    expect(triggers).toContain("15:every_nth");
    expect(triggers).toContain("20:every_nth");
  });
});

describe("FrameSampler — reset", () => {
  it("reset clears the frame counter and intensity baseline", () => {
    const sampler = new FrameSampler({ everyN: 3, disableSceneChange: true });
    sampler.consider(uniformFrame(100));
    sampler.consider(uniformFrame(100));
    expect(sampler.getFrameNumber()).toBe(2);
    sampler.reset();
    expect(sampler.getFrameNumber()).toBe(0);
    // After reset, the next every-Nth trigger should be at frame 3 again
    sampler.consider(uniformFrame(100));
    sampler.consider(uniformFrame(100));
    const d = sampler.consider(uniformFrame(100));
    expect(d.should_describe).toBe(true);
  });
});

describe("FrameSampler — savings", () => {
  it("reduces vision-model calls by ~95% in typical conditions", () => {
    // Simulate 30 seconds at 30fps = 900 frames with occasional scene changes
    const sampler = new FrameSampler({
      everyN: 30,                     // 1 fps baseline
      sceneChangeThreshold: 0.08,
      cooldownFrames: 5,
    });
    let triggers = 0;
    for (let i = 0; i < 900; i++) {
      // Mostly-static scene with 5 scene changes spread evenly
      const intensity = (i % 180 === 0) ? 220 : 60 + (i % 4);
      const d = sampler.consider(uniformFrame(intensity));
      if (d.should_describe) triggers++;
    }
    // Expect ~30 baseline triggers + ~5 scene-change triggers ≈ 35
    // Anything under 60 means we saved at least 93%
    expect(triggers).toBeLessThan(60);
    const savingsRatio = 1 - triggers / 900;
    expect(savingsRatio).toBeGreaterThan(0.93);
  });
});
