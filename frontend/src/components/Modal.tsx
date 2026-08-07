import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";

interface Props {
  title: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  width?: string;
}

export function Modal({ title, open, onClose, children, width = "max-w-lg" }: Props) {
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        className={`relative w-full ${width} max-h-[90vh] overflow-y-auto rounded-xl border border-border bg-surface shadow-2xl`}
      >
        <div className="flex items-center justify-between border-b border-border p-5">
          <h2 className="text-lg font-semibold text-text">{title}</h2>
          <button
            onClick={onClose}
            aria-label="Fechar"
            className="p-1 text-muted transition-colors hover:text-text"
          >
            <X size={18} />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}
