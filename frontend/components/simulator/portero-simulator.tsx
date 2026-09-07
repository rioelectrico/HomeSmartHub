"use client";

import { BellRing, Cable, MessageSquare, Mic, PhoneOff, ShieldCheck, Unplug } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { PorteroAudioSession } from "@/lib/simulator/audio-session";
import { PorteroDeviceClient } from "@/lib/simulator/device-client";
import type { ClientErrorCode, ClientState } from "@/lib/simulator/contracts";

type Phase = "disconnected" | "connecting" | "connected" | "preparing" | "listening" | "visitor_speaking" | "assistant_speaking" | "error";
type Transcript = { id: string; role: "user" | "assistant"; text: string };
const labels: Record<Phase, string> = {
  disconnected: "Desconectado", connecting: "Conectando", connected: "Conectado",
  preparing: "Preparando", listening: "Escuchando", visitor_speaking: "Visitante hablando",
  assistant_speaking: "Agente hablando", error: "Error",
};
const phaseFor = (state: ClientState): Phase => {
  if (state === "ready") return "connected";
  if (state === "authenticating") return "connecting";
  if (state === "starting" || state === "stopping") return "preparing";
  return state;
};
const errors: Partial<Record<ClientErrorCode, string>> = {
  AUTH_FAILED: "No se pudo autenticar el dispositivo. Revisá su identificador y volvé a ingresar el secreto vigente.",
  AI_UNCONFIGURED: "El agente de voz no está configurado. Revisá la configuración del backend antes de volver a tocar el timbre.",
  AI_UNAVAILABLE: "El agente de voz no está disponible. Podés volver a tocar el timbre para reintentar.",
  CONNECTION_REPLACED: "Otra conexión está usando este dispositivo. Desconectala antes de volver a conectar.",
  CONNECTION_ERROR: "Se perdió la conexión. Revisá que el backend esté disponible y volvé a conectar.",
  CONVERSATION_TIMEOUT: "La visita alcanzó su duración máxima. Podés iniciar una nueva visita.",
  CONVERSATION_IDLE_TIMEOUT: "La visita terminó por inactividad. Podés volver a tocar el timbre.",
  AUDIO_INPUT_OVERFLOW: "El audio de entrada se saturó. La visita se detuvo; verificá la conexión y reintentá.",
  AUDIO_OUTPUT_OVERFLOW: "El audio de salida se saturó. La visita se detuvo; podés reintentar.",
};

function audioError(error: unknown): string {
  const name = error instanceof Error || error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") return "Para hablar, permití el micrófono en el navegador y volvé a tocar el timbre.";
  if (name === "NotFoundError") return "No se encontró un micrófono. Conectá uno y volvé a tocar el timbre.";
  if (name === "NotReadableError") return "No se pudo usar el micrófono. Cerrá otras aplicaciones que lo estén usando y reintentá.";
  return "No se pudo preparar el audio. Usá un navegador compatible en localhost o HTTPS, revisá el micrófono y reintentá.";
}

export function PorteroSimulator() {
  const [deviceId, setDeviceId] = useState("PI-000001");
  const [phase, setPhase] = useState<Phase>("disconnected");
  const [clientState, setClientState] = useState<ClientState>("disconnected");
  const [visiting, setVisiting] = useState(false);
  const [closing, setClosing] = useState(false);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [validation, setValidation] = useState<{ device?: string; secret?: string }>({});
  const [transcript, setTranscript] = useState<Transcript[]>([]);
  const idInput = useRef<HTMLInputElement>(null);
  const secretInput = useRef<HTMLInputElement>(null);
  const client = useRef<PorteroDeviceClient | null>(null);
  const audio = useRef<PorteroAudioSession | null>(null);
  const mounted = useRef(false);
  const generation = useRef(0);
  const active = useRef(false);
  const errorRef = useRef<string | null>(null);
  const cleanupAudio = useRef<() => void>(() => {});

  useEffect(() => {
    mounted.current = true;
    let alive = true;
    const passwordInput = secretInput.current;
    const session = new PorteroAudioSession();
    audio.current = session;
    let cleanupVersion = 0;
    const closeAudio = () => {
      active.current = false;
      generation.current++;
      const version = ++cleanupVersion;
      if (alive) { setVisiting(false); setClosing(true); setLevel(0); }
      session.clearPlayback();
      void session.close().finally(() => {
        if (alive && cleanupVersion === version) setClosing(false);
      });
    };
    cleanupAudio.current = closeAudio;
    const device = new PorteroDeviceClient({
      onState(state) {
        if (!alive) return;
        setClientState(state);
        if (state === "disconnected" || state === "ready") {
          if (active.current) closeAudio();
          if (secretInput.current) secretInput.current.value = "";
        }
        setPhase(errorRef.current ? "error" : phaseFor(state));
      },
      onTranscript(message) {
        if (!alive || !message.final) return;
        const id = `${message.conversation_id}:${message.seq}`;
        setTranscript((entries) => entries.some((entry) => entry.id === id) ? entries : [...entries, { id, role: message.role, text: message.text }]);
      },
      onAudio(samples) { if (alive && active.current) session.enqueuePlayback(samples); },
      onAudioClear() { if (alive) session.clearPlayback(); },
      onEnded() { if (alive) closeAudio(); },
      onError(code) {
        if (!alive) return;
        closeAudio();
        // A public conversation error may arrive before its terminal message.
        // Keep start disabled until the transport returns to ready.
        const message = errors[code] ?? "La visita se interrumpió. Finalizá la conexión y volvé a conectar para reintentar.";
        errorRef.current = message; setError(message); setPhase("error");
      },
    });
    client.current = device;
    return () => {
      alive = false;
      mounted.current = false;
      if (passwordInput) passwordInput.value = "";
      closeAudio();
      device.disconnect();
      client.current = null;
      audio.current = null;
    };
  }, []);

  function resetError() { errorRef.current = null; setError(null); }

  function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (client.current?.state !== "disconnected") return;
    const issues: { device?: string; secret?: string } = {};
    if (!/^PI-[0-9]{6}$/.test(deviceId)) issues.device = "Usá el formato PI-000001: PI- seguido de seis números.";
    const secret = secretInput.current?.value ?? "";
    if (secret.length < 32 || secret.length > 128) issues.secret = "Ingresá el secreto vigente del dispositivo (entre 32 y 128 caracteres).";
    setValidation(issues);
    if (issues.device || issues.secret) { (issues.device ? idInput : secretInput).current?.focus(); return; }
    resetError();
    // Uncontrolled password input avoids retaining credentials in React state.
    // Clear before handoff, including synchronous connection failures.
    secretInput.current!.value = "";
    try { client.current.connect({ deviceId, secret }); }
    catch {
      client.current.disconnect();
      const message = "No se pudo conectar. Revisá la dirección del backend y usá un navegador compatible en localhost o HTTPS.";
      errorRef.current = message; setError(message); setPhase("error");
    }
  }

  async function ring() {
    const device = client.current;
    const session = audio.current;
    if (!device || !session || device.state !== "ready" || active.current || closing) return;
    resetError(); setTranscript([]); setPhase("preparing"); setVisiting(true);
    active.current = true;
    const attempt = ++generation.current;
    try {
      await session.start((frame) => {
        if (mounted.current && active.current && generation.current === attempt && device.streamId
          && ["preparing", "listening", "visitor_speaking", "assistant_speaking"].includes(device.state)) device.sendAudio(frame);
      }, (rms) => {
        if (mounted.current && active.current && generation.current === attempt) setLevel(Math.max(0, Math.min(1, Number.isFinite(rms) ? rms : 0)));
      }, () => {
        if (!mounted.current || !active.current || generation.current !== attempt) return;
        const conversationId = device.conversationId;
        const streamId = device.streamId;
        if (conversationId && streamId) device.reportAudioOutputOverflow(conversationId, streamId);
      });
      if (!mounted.current || !active.current || generation.current !== attempt) return;
      device.startConversation();
    } catch (cause) {
      if (!mounted.current || generation.current !== attempt) return;
      cleanupAudio.current();
      const message = audioError(cause);
      errorRef.current = message; setError(message); setPhase("error");
    }
  }

  function finish() {
    const device = client.current;
    cleanupAudio.current();
    if (!device) return;
    if (device.conversationId && device.state !== "stopping") {
      try { device.stopConversation(); } catch { device.disconnect(); }
    } else if (device.state === "starting") device.disconnect();
    else setPhase(errorRef.current ? "error" : phaseFor(device.state));
  }

  function disconnect() {
    resetError(); cleanupAudio.current(); client.current?.disconnect(); setPhase("disconnected");
    if (secretInput.current) secretInput.current.value = "";
  }

  const disconnected = clientState === "disconnected";
  const canRing = clientState === "ready" && !visiting && !closing;

  return (
    <main className="portero-simulator">
      <header className="portero-simulator__heading">
        <div className="portero-simulator__mark"><BellRing aria-hidden="true" /></div>
        <div><p className="eyebrow">Portero inteligente · Simulador</p><h1>Simulador de portero</h1><p>Probá una visita por voz, manos libres.</p></div>
      </header>
      <div className="portero-simulator__grid">
        <section className="panel-card" aria-labelledby="connection-title">
          <h2 id="connection-title">Conexión del dispositivo</h2>
          <p className="muted">Ingresá las credenciales del portero que querés probar.</p>
          <form onSubmit={connect} noValidate autoComplete="off">
            <div className="field">
              <label htmlFor="simulator-device">Identificador del dispositivo</label>
              <input ref={idInput} id="simulator-device" value={deviceId} onChange={(event) => setDeviceId(event.target.value)} disabled={!disconnected} required pattern="PI-[0-9]{6}" maxLength={32} autoCapitalize="characters" spellCheck={false} aria-invalid={!!validation.device} aria-describedby={validation.device ? "simulator-device-error" : undefined} />
              {validation.device && <p className="form-error" id="simulator-device-error">{validation.device}</p>}
            </div>
            <div className="field">
              <label htmlFor="simulator-secret">Secreto del dispositivo</label>
              <input ref={secretInput} id="simulator-secret" type="password" disabled={!disconnected} required minLength={32} maxLength={128} autoComplete="off" aria-invalid={!!validation.secret} aria-describedby={`simulator-secret-help${validation.secret ? " simulator-secret-error" : ""}`} />
              {validation.secret && <p className="form-error" id="simulator-secret-error">{validation.secret}</p>}
            </div>
            <p className="muted portero-simulator__privacy" id="simulator-secret-help"><ShieldCheck aria-hidden="true" />El secreto sólo vive en memoria y se borra al conectar. Para reconectar, ingresalo nuevamente.</p>
            <div className="actions">
              <button className="button button--primary" disabled={!disconnected} type="submit"><Cable aria-hidden="true" />Conectar</button>
              <button className="button button--secondary" disabled={disconnected} type="button" onClick={disconnect}><Unplug aria-hidden="true" />Desconectar</button>
            </div>
          </form>
        </section>
        <section className="panel-card portero-simulator__device" aria-labelledby="visit-title">
          <div className="portero-simulator__state" role="status" aria-live="polite" aria-atomic="true"><span className={`portero-simulator__dot portero-simulator__dot--${phase}`} aria-hidden="true" />{labels[phase]}{clientState === "stopping" && <span> · Finalizando visita</span>}</div>
          <h2 id="visit-title">Tu visita</h2>
          <p className="muted">Tocá el timbre y permití el micrófono. Hablá con naturalidad; podés interrumpir al agente en cualquier momento.</p>
          <button className="button button--primary portero-simulator__bell" type="button" disabled={!canRing} onClick={() => void ring()}><BellRing aria-hidden="true" />Tocar timbre</button>
          <div className="portero-simulator__level"><label htmlFor="simulator-level"><Mic aria-hidden="true" />Nivel del micrófono</label><meter id="simulator-level" min={0} max={1} value={level}>{Math.round(level * 100)}%</meter><p className="muted">{visiting ? "Micrófono activo durante la visita." : "Micrófono apagado."}</p></div>
          <button className="button button--secondary" type="button" disabled={!visiting || clientState === "stopping"} onClick={finish}><PhoneOff aria-hidden="true" />Finalizar visita</button>
        </section>
      </div>
      {error && <div className="portero-simulator__error" role="alert">{error}</div>}
      <section className="panel-card portero-simulator__transcript" aria-labelledby="transcript-title">
        <h2 id="transcript-title"><MessageSquare aria-hidden="true" />Transcripción de la visita</h2>
        <div role="log" aria-label="Transcripción de la visita" aria-live="polite" aria-relevant="additions">
          {transcript.length === 0 ? <p className="muted">Las intervenciones completas del visitante y del agente aparecerán acá.</p> : <ol>{transcript.map((entry) => <li key={entry.id} className={`portero-simulator__message portero-simulator__message--${entry.role}`}><span>{entry.role === "user" ? "Visitante" : "Agente"}</span><p>{entry.text}</p></li>)}</ol>}
        </div>
      </section>
    </main>
  );
}
