"use client";

import * as Dialog from "@radix-ui/react-dialog";

export function ConfirmDialog({ title, description, confirmLabel, busy = false, error, trigger, onClose, onConfirm }: {
  title: string;
  description: string;
  confirmLabel: string;
  busy?: boolean;
  error?: string;
  trigger?: HTMLButtonElement;
  onClose: () => void;
  onConfirm: () => void | Promise<void>;
}) {
  return <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose(); }}><Dialog.Portal>
    <Dialog.Overlay className="dialog-overlay" />
    <Dialog.Content className="dialog-content" onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }} onPointerDownOutside={(event) => { if (busy) event.preventDefault(); }} onCloseAutoFocus={(event) => { if (trigger) { event.preventDefault(); trigger.focus(); } }}>
      <Dialog.Title>{title}</Dialog.Title>
      <Dialog.Description>{description}</Dialog.Description>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="actions">
        <button className="button button--primary" type="button" disabled={busy} onClick={() => void onConfirm()}>{busy ? "Procesando…" : confirmLabel}</button>
        <button className="button button--secondary" type="button" disabled={busy} onClick={onClose}>Cancelar</button>
      </div>
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>;
}
