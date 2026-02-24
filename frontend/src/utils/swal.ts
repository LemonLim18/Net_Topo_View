/**
 * SweetAlert2 — themed utility wrappers for the app.
 *
 * Usage:
 *   import { Toast, Confirm, Alert } from '../src/utils/swal';
 *
 *   Toast.success('Device added');
 *   Toast.error('Something broke', 'Check your network');
 *   const ok = await Confirm.danger('Delete device R1?', 'This cannot be undone.');
 *   Alert.info('Heads up', 'The sync will run in the background.');
 */
import Swal from 'sweetalert2';

// ---------------------------------------------------------------------------
//  Base mixin — follows DaisyUI dark/light via CSS variables
// ---------------------------------------------------------------------------
const baseMixin = Swal.mixin({
  customClass: {
    popup: '!bg-base-100 !text-base-content !rounded-xl !shadow-2xl',
    title: '!text-base-content',
    htmlContainer: '!text-base-content/80',
    confirmButton: 'btn btn-primary btn-sm',
    cancelButton: 'btn btn-ghost btn-sm',
    denyButton: 'btn btn-error btn-sm',
  },
  buttonsStyling: false,         // let DaisyUI classes handle buttons
  showClass: { popup: 'animate-in fade-in zoom-in-95 duration-200' },
  hideClass: { popup: 'animate-out fade-out zoom-out-95 duration-150' },
});

// ---------------------------------------------------------------------------
//  Toast — auto-dismissing corner notification
// ---------------------------------------------------------------------------
const ToastBase = baseMixin.mixin({
  toast: true,
  position: 'top-end',
  showConfirmButton: false,
  timer: 3500,
  timerProgressBar: true,
  didOpen: (toast) => {
    toast.addEventListener('mouseenter', Swal.stopTimer);
    toast.addEventListener('mouseleave', Swal.resumeTimer);
  },
});

export const Toast = {
  success: (title: string, text?: string) =>
    ToastBase.fire({ icon: 'success', title, text }),

  error: (title: string, text?: string) =>
    ToastBase.fire({ icon: 'error', title, text }),

  warning: (title: string, text?: string) =>
    ToastBase.fire({ icon: 'warning', title, text }),

  info: (title: string, text?: string) =>
    ToastBase.fire({ icon: 'info', title, text }),
};

// ---------------------------------------------------------------------------
//  Confirm — centered dialog that returns a boolean
// ---------------------------------------------------------------------------
export const Confirm = {
  /** Red "danger" confirm (e.g. delete). Returns true if confirmed. */
  danger: async (title: string, text?: string): Promise<boolean> => {
    const result = await baseMixin.fire({
      icon: 'warning',
      title,
      text,
      showCancelButton: true,
      confirmButtonText: 'Yes, proceed',
      cancelButtonText: 'Cancel',
      customClass: {
        popup: '!bg-base-100 !text-base-content !rounded-xl !shadow-2xl',
        title: '!text-base-content',
        htmlContainer: '!text-base-content/80',
        confirmButton: 'btn btn-error btn-sm',
        cancelButton: 'btn btn-ghost btn-sm',
      },
      reverseButtons: true,
    });
    return result.isConfirmed;
  },

  /** Neutral confirm (e.g. rollback). Returns true if confirmed. */
  ask: async (title: string, text?: string): Promise<boolean> => {
    const result = await baseMixin.fire({
      icon: 'question',
      title,
      text,
      showCancelButton: true,
      confirmButtonText: 'Confirm',
      cancelButtonText: 'Cancel',
      reverseButtons: true,
    });
    return result.isConfirmed;
  },
};

// ---------------------------------------------------------------------------
//  Alert — centered informational dialog (single OK button)
// ---------------------------------------------------------------------------
export const Alert = {
  success: (title: string, text?: string) =>
    baseMixin.fire({ icon: 'success', title, text }),

  error: (title: string, text?: string) =>
    baseMixin.fire({ icon: 'error', title, text }),

  warning: (title: string, text?: string) =>
    baseMixin.fire({ icon: 'warning', title, text }),

  info: (title: string, text?: string) =>
    baseMixin.fire({ icon: 'info', title, text }),
};
