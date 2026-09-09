import { describe, expect, it } from "vitest";
import {
  buildAgentSessionOptions,
  readAgentSessionControls,
} from "@/lib/openai-session-options";

describe("OpenAI session option controls", () => {
  it("reads persisted OpenAI options into user-facing controls", () => {
    expect(
      readAgentSessionControls({
        audio: {
          input: {
            noise_reduction: { type: "far_field" },
            turn_detection: {
              type: "semantic_vad",
              eagerness: "low",
              interrupt_response: false,
            },
          },
        },
        max_output_tokens: 600,
        reasoning: { effort: "high" },
      })
    ).toEqual({
      microphoneEnvironment: "far_field",
      turnDetection: "semantic_vad",
      responseTiming: "low",
      allowInterruptions: false,
      responseLength: "detailed",
      reasoningEffort: "high",
    });
  });

  it("builds managed options while preserving unrelated persisted settings", () => {
    expect(
      buildAgentSessionOptions(
        {
          custom_setting: "keep-me",
          audio: { input: { transcription: { keywords: ["Portero"] } } },
        },
        {
          microphoneEnvironment: "near_field",
          turnDetection: "server_vad",
          responseTiming: "auto",
          allowInterruptions: true,
          responseLength: "normal",
          reasoningEffort: "low",
        },
        "gpt-realtime-2.1"
      )
    ).toEqual({
      custom_setting: "keep-me",
      audio: {
        input: {
          transcription: { keywords: ["Portero"] },
          noise_reduction: { type: "near_field" },
          turn_detection: {
            type: "server_vad",
            create_response: true,
            interrupt_response: true,
          },
        },
      },
      max_output_tokens: 300,
      reasoning: { effort: "low" },
    });
  });

  it("disables automatic turn detection and omits reasoning for unsupported models", () => {
    expect(
      buildAgentSessionOptions(
        { reasoning: { effort: "high" } },
        {
          microphoneEnvironment: "off",
          turnDetection: "manual",
          responseTiming: "auto",
          allowInterruptions: false,
          responseLength: "unlimited",
          reasoningEffort: "medium",
        },
        "gpt-realtime-1.5"
      )
    ).toEqual({
      audio: {
        input: {
          noise_reduction: null,
          turn_detection: null,
        },
      },
      max_output_tokens: "inf",
    });
  });
});
