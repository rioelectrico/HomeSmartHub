export interface ApiErrorEnvelope {
  error: {
    code: string;
    message?: string;
  };
}

export interface HomeAccess {
  id: string;
  name: string;
  timezone: string;
  role: string;
  permissions: string[];
}

export interface CurrentUser {
  id: string;
  username: string;
  email: string;
  homes: HomeAccess[];
}

export interface AgentConfig {
  name: string;
  system_prompt: string;
  language: string;
  voice: string;
  voice_speed: number;
  realtime_model: string;
  openai_session_options: Record<string, unknown>;
  enabled: boolean;
  updated_at: string | null;
}

export interface ActivityCounts { events: number; conversations: number; captures: number }
export interface Statistics {
  timezone: string;
  generated_at: string;
  today: ActivityCounts;
  last_7_days: ActivityCounts;
  daily: (ActivityCounts & { date: string })[];
  devices: { total: number; online: number; offline: number; disabled: number; provisioning: number };
}
export interface DeviceDiagnostic {
  id: string;
  device_id: string;
  name: string;
  status: "online" | "offline" | "disabled" | "provisioning";
  connected: boolean;
  last_seen_at: string | null;
  snapshot_at: string | null;
  snapshot: Record<string, unknown> | null;
}
export interface Diagnostics {
  services: { backend: "up"; postgresql: "up" | "down"; openai: "configured" | "unconfigured" | "available" | "unavailable" };
  devices: { items: DeviceDiagnostic[]; next_cursor: string | null };
}

export interface HomeUser { id: string; username: string; email: string; role: string; is_active: boolean }
export interface HomeUsers { items: HomeUser[] }

export type DeviceStatus = "online" | "offline" | "provisioning" | "disabled";
export interface Device {
  id: string;
  home_id: string;
  device_id: string;
  name: string;
  status: DeviceStatus;
  last_seen_at: string | null;
  created_at: string;
  updated_at: string;
}
export interface Devices { items: Device[] }
export interface ProvisionedDevice extends Device { secret: string }

export type CommandStatus = "pending" | "sent" | "acknowledged" | "completed" | "failed" | "timeout";
export interface CommandTimelineItem { status: CommandStatus; at: string }
export interface DeviceCommand {
  command_id: string;
  device_id: string;
  requested_by_user_id: string | null;
  command: string;
  payload: Record<string, unknown>;
  status: CommandStatus;
  result: Record<string, unknown> | null;
  created_at: string;
  timeline: CommandTimelineItem[];
}

export interface HomeEvent {
  id: string;
  home_id: string;
  device_id: string | null;
  event_type: string;
  created_at: string;
  payload: Record<string, unknown>;
}
export interface EventsPage { items: HomeEvent[]; next_cursor: string | null }

export interface AuditEntry {
  id: string;
  home_id: string | null;
  user_id: string | null;
  action: string;
  detail: string | null;
  context: Record<string, unknown>;
  created_at: string;
}
export interface AuditPage { items: AuditEntry[]; next_cursor: string | null }
