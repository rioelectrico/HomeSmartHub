import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ClientState, DeviceClientCallbacks, TranscriptMessage } from "@/lib/simulator/contracts";
import SimulatorPage from "@/app/simulador-portero/page";
import { StrictMode } from "react";

const mocks = vi.hoisted(() => ({ clients: [] as FakeClient[], audios: [] as FakeAudio[] }));
class FakeClient {
  state: ClientState = "disconnected";
  conversationId: string | undefined;
  streamId: string | undefined;
  constructor(public callbacks: DeviceClientCallbacks) { mocks.clients.push(this); }
  transition(state: ClientState) { this.state = state; this.callbacks.onState?.(state); }
  connect = vi.fn(() => this.transition("connecting"));
  startConversation = vi.fn(() => this.transition("starting"));
  stopConversation = vi.fn(() => this.transition("stopping"));
  reportAudioOutputOverflow = vi.fn(() => { this.transition("stopping"); this.callbacks.onError?.("AUDIO_OUTPUT_OVERFLOW"); });
  sendAudio = vi.fn();
  disconnect = vi.fn(() => { this.conversationId = undefined; this.streamId = undefined; this.transition("disconnected"); });
}
class FakeAudio {
  frame?: (data: Int16Array) => void;
  level?: (value: number) => void;
  error?: (code: "AUDIO_OUTPUT_OVERFLOW") => void;
  constructor() { mocks.audios.push(this); }
  start = vi.fn(async (frame: (data: Int16Array) => void, level: (value: number) => void, error?: (code: "AUDIO_OUTPUT_OVERFLOW") => void) => { this.frame = frame; this.level = level; this.error = error; });
  enqueuePlayback = vi.fn();
  clearPlayback = vi.fn();
  close = vi.fn(async () => {});
}
vi.mock("@/lib/simulator/device-client", () => ({ PorteroDeviceClient: class { constructor(callbacks: DeviceClientCallbacks) { return new FakeClient(callbacks); } } }));
vi.mock("@/lib/simulator/audio-session", () => ({ PorteroAudioSession: class { constructor() { return new FakeAudio(); } } }));
const secret = "test-device-secret-12345678901234567890";
const client = () => mocks.clients.at(-1)!;
const audio = () => mocks.audios.at(-1)!;
const click = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
function credentials(id = "PI-000001", value = secret) {
  fireEvent.change(screen.getByLabelText("Identificador del dispositivo"), { target: { value: id } });
  fireEvent.change(screen.getByLabelText("Secreto del dispositivo"), { target: { value } });
}
function connect() { credentials(); click("Conectar"); act(() => client().transition("ready")); }
async function ring() { click("Tocar timbre"); await waitFor(() => expect(client().startConversation).toHaveBeenCalledTimes(1)); }
function started(state: ClientState = "listening") {
  act(() => { client().conversationId = "conversation-1"; client().streamId = "stream-1"; client().transition(state); });
}
function transcript(seq: number, role: "user" | "assistant", text: string): TranscriptMessage {
  return { type: "conversation.transcript", version: 1, seq, conversation_id: "conversation-1", role, text, final: true };
}
beforeEach(() => { mocks.clients.length = 0; mocks.audios.length = 0; });
afterEach(cleanup);

describe("independent hands-free simulator", () => {
  it("terminates saturated local playback, shows the public error and waits for durable ended before retry", async () => {
    render(<SimulatorPage />); connect(); await ring(); started();
    const oldError = audio().error;
    act(() => oldError?.("AUDIO_OUTPUT_OVERFLOW"));
    expect(screen.getByRole("alert")).toHaveTextContent("El audio de salida se saturó");
    expect(screen.getByRole("status")).toHaveTextContent("Error");
    expect(screen.getByText("Micrófono apagado.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Tocar timbre" })).toBeDisabled();
    expect(client().reportAudioOutputOverflow).toHaveBeenCalledExactlyOnceWith("conversation-1", "stream-1");
    expect(client().disconnect).not.toHaveBeenCalled();
    act(() => { audio().frame?.(new Int16Array(480)); client().callbacks.onAudio?.(new Int16Array(480)); oldError?.("AUDIO_OUTPUT_OVERFLOW"); });
    expect(client().sendAudio).not.toHaveBeenCalled();
    expect(audio().enqueuePlayback).not.toHaveBeenCalled();
    act(() => { client().conversationId = undefined; client().streamId = undefined; client().transition("ready"); client().callbacks.onEnded?.({ type: "conversation.ended", version: 1, seq: 10, conversation_id: "conversation-1", reason: "audio_output_overflow", outcome: "failed" }); });
    await waitFor(() => expect(screen.getByRole("button", { name: "Tocar timbre" })).toBeEnabled());
    click("Tocar timbre");
    await waitFor(() => expect(client().startConversation).toHaveBeenCalledTimes(2));
    started();
    act(() => oldError?.("AUDIO_OUTPUT_OVERFLOW"));
    expect(client().reportAudioOutputOverflow).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("renders a public screen with microphone off and no administrative navigation", () => {
    render(<SimulatorPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Simulador de portero");
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Desconectado");
    expect(screen.getByRole("button", { name: "Tocar timbre" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Finalizar visita" })).toBeDisabled();
    expect(audio().start).not.toHaveBeenCalled();
  });
  it.each(["PI-12345", "PI-1234567", "pi-000001", "XX-000001"])("rejects invalid ID %s inline and focuses it", (id) => {
    render(<SimulatorPage />); credentials(id); click("Conectar");
    expect(screen.getByLabelText("Identificador del dispositivo")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Identificador del dispositivo")).toHaveFocus();
    expect(screen.getByText(/Usá el formato PI-000001/)).toBeVisible();
    expect(client().connect).not.toHaveBeenCalled();
  });
  it.each(["", "short"])("requires a valid device secret", (value) => {
    render(<SimulatorPage />); credentials("PI-000001", value); click("Conectar");
    expect(screen.getByLabelText("Secreto del dispositivo")).toHaveFocus();
    expect(screen.getByLabelText("Secreto del dispositivo")).toHaveAttribute("aria-invalid", "true");
    expect(client().connect).not.toHaveBeenCalled();
  });
  it("hands off credentials once, clears the secret, and never acquires microphone on authentication", () => {
    render(<SimulatorPage />); credentials(); click("Conectar");
    expect(client().connect).toHaveBeenCalledWith({ deviceId: "PI-000001", secret });
    expect(screen.getByLabelText("Secreto del dispositivo")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Conectar" })).toBeDisabled();
    act(() => client().transition("ready"));
    expect(screen.getByRole("button", { name: "Tocar timbre" })).toBeEnabled();
    expect(audio().start).not.toHaveBeenCalled();
  });
  it("waits for microphone readiness before starting, then forwards audio only for a live stream", async () => {
    render(<SimulatorPage />); connect();
    let resolve!: () => void;
    audio().start.mockImplementationOnce((frame, level) => { audio().frame = frame; audio().level = level; return new Promise<void>((done) => { resolve = done; }); });
    click("Tocar timbre"); click("Tocar timbre");
    expect(audio().start).toHaveBeenCalledTimes(1);
    expect(client().startConversation).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("Preparando");
    act(() => audio().frame?.(new Int16Array(480)));
    expect(client().sendAudio).not.toHaveBeenCalled();
    await act(async () => resolve());
    started();
    const frame = new Int16Array(480);
    act(() => { audio().frame?.(frame); audio().level?.(0.25); });
    expect(client().sendAudio).toHaveBeenCalledWith(frame);
    expect(screen.getByRole("meter", { name: "Nivel del micrófono" })).toHaveAttribute("value", "0.25");
    act(() => client().callbacks.onAudio?.(frame));
    expect(audio().enqueuePlayback).toHaveBeenCalledWith(frame);
  });
  it.each([["preparing", "Preparando"], ["listening", "Escuchando"], ["visitor_speaking", "Visitante hablando"], ["assistant_speaking", "Agente hablando"], ["error", "Error"]] as const)("localizes %s in a live status", async (state, label) => {
    render(<SimulatorPage />); connect(); await ring(); started(state);
    expect(screen.getByRole("status")).toHaveTextContent(label);
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
  });
  it("renders final transcripts by participant, escapes text and deduplicates repeated delivery", async () => {
    render(<SimulatorPage />); connect(); await ring(); started();
    act(() => {
      client().callbacks.onTranscript?.(transcript(2, "user", "Soy Juan"));
      client().callbacks.onTranscript?.(transcript(2, "user", "Soy Juan"));
      client().callbacks.onTranscript?.(transcript(3, "assistant", "<script>Buen día</script>"));
      client().callbacks.onTranscript?.({ ...transcript(4, "assistant", "partial"), final: false } as unknown as TranscriptMessage);
    });
    const log = screen.getByRole("log", { name: "Transcripción de la visita" });
    expect(within(log).getAllByText("Soy Juan")).toHaveLength(1);
    expect(within(log).getByText("Visitante")).toBeVisible();
    expect(within(log).getByText("Agente")).toBeVisible();
    expect(within(log).getByText("<script>Buen día</script>")).toBeVisible();
    expect(log.querySelector("script")).toBeNull();
    expect(within(log).queryByText("partial")).not.toBeInTheDocument();
  });
  it("delegates barge-in clear and closes local audio immediately on finish", async () => {
    render(<SimulatorPage />); connect(); await ring(); started();
    act(() => client().callbacks.onAudioClear?.({ type: "conversation.audio.clear", version: 1, seq: 9, conversation_id: "conversation-1", stream_id: "stream-1", reason: "barge_in" }));
    expect(audio().clearPlayback).toHaveBeenCalled();
    click("Finalizar visita");
    expect(client().stopConversation).toHaveBeenCalledTimes(1);
    expect(audio().close).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Tocar timbre" })).toBeDisabled();
    act(() => { client().conversationId = undefined; client().streamId = undefined; client().transition("ready"); client().callbacks.onEnded?.({ type: "conversation.ended", version: 1, seq: 10, conversation_id: "conversation-1", reason: "visitor_finished", outcome: "registered" }); });
    await waitFor(() => expect(screen.getByRole("button", { name: "Tocar timbre" })).toBeEnabled());
  });
  it("allows retry after denied permission without reconnecting or exposing the exception", async () => {
    render(<SimulatorPage />); connect();
    audio().start.mockRejectedValueOnce(new DOMException("secret raw error", "NotAllowedError"));
    click("Tocar timbre");
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/permití el micrófono/i));
    expect(screen.queryByText(/secret raw error/)).not.toBeInTheDocument();
    expect(client().startConversation).not.toHaveBeenCalled();
    expect(client().disconnect).not.toHaveBeenCalled();
    await ring(); expect(audio().start).toHaveBeenCalledTimes(2);
  });
  it("cancels a pending permission request without allowing late readiness to start a visit", async () => {
    render(<SimulatorPage />); connect();
    let resolve!: () => void;
    audio().start.mockImplementationOnce(() => new Promise<void>((done) => { resolve = done; }));
    click("Tocar timbre"); click("Finalizar visita");
    await act(async () => resolve());
    expect(client().startConversation).not.toHaveBeenCalled();
    expect(audio().close).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Tocar timbre" })).toBeEnabled();
  });
  it("disconnects to cancel a server start that has not assigned a conversation ID", async () => {
    render(<SimulatorPage />); connect(); await ring(); click("Finalizar visita");
    expect(client().disconnect).toHaveBeenCalledTimes(1);
    expect(client().stopConversation).not.toHaveBeenCalled();
  });
  it("cleans audio on provider errors and enables another visit when server is ready", async () => {
    render(<SimulatorPage />); connect(); await ring();
    act(() => { client().transition("ready"); client().callbacks.onError?.("AI_UNCONFIGURED"); });
    expect(screen.getByRole("alert")).toHaveTextContent(/configur/i);
    expect(audio().close).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("button", { name: "Tocar timbre" })).toBeEnabled());
  });
  it("cleans resources on disconnect and ignores late callbacks after unmount", async () => {
    const view = render(<SimulatorPage />); connect(); await ring(); started();
    click("Desconectar");
    expect(client().disconnect).toHaveBeenCalledTimes(1);
    expect(audio().close).toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("Desconectado");
    view.unmount();
    const calls = audio().enqueuePlayback.mock.calls.length;
    act(() => client().callbacks.onAudio?.(new Int16Array(480)));
    expect(audio().enqueuePlayback).toHaveBeenCalledTimes(calls);
  });
  it("waits for audio cleanup before enabling another visit", async () => {
    render(<SimulatorPage />); connect(); await ring(); started();
    let resolve!: () => void;
    audio().close.mockImplementation(() => new Promise<void>((done) => { resolve = done; }));
    act(() => { client().conversationId = undefined; client().streamId = undefined; client().transition("ready"); });
    expect(screen.getByRole("button", { name: "Tocar timbre" })).toBeDisabled();
    await act(async () => resolve());
    expect(screen.getByRole("button", { name: "Tocar timbre" })).toBeEnabled();
  });
  it("does not let callbacks from a disposed Strict Mode connection mutate the current UI", () => {
    render(<StrictMode><SimulatorPage /></StrictMode>);
    expect(mocks.clients).toHaveLength(2);
    act(() => mocks.clients[0].callbacks.onError?.("AUTH_FAILED"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Desconectado");
  });
  it.each(["NotFoundError", "NotReadableError"])("shows actionable %s audio errors", async (name) => {
    render(<SimulatorPage />); connect();
    audio().start.mockRejectedValueOnce(new DOMException("private", name));
    click("Tocar timbre");
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(name === "NotFoundError" ? /Conectá uno/ : /Cerrá otras aplicaciones/));
    expect(client().startConversation).not.toHaveBeenCalled();
  });
  it("does not display a synchronous connection exception or retain its secret", () => {
    render(<SimulatorPage />); credentials();
    client().connect.mockImplementationOnce(() => { throw new Error(secret); });
    click("Conectar");
    expect(screen.getByLabelText("Secreto del dispositivo")).toHaveValue("");
    expect(screen.getByRole("alert")).toHaveTextContent("No se pudo conectar");
    expect(screen.queryByText(secret)).not.toBeInTheDocument();
  });
});
