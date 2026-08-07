interface Props {
  message: string;
  variant?: "error" | "success";
  onDismiss: () => void;
}

export function Toast({ message, variant = "error", onDismiss }: Props) {
  const estilo =
    variant === "error"
      ? "border-danger/40 bg-danger/10 text-danger"
      : "border-success/40 bg-success/10 text-success";

  return (
    <div
      className={`fixed inset-x-4 bottom-4 z-50 mx-auto flex max-w-md items-center justify-between gap-3 rounded-lg border px-4 py-3 text-sm shadow-lg backdrop-blur sm:inset-x-auto sm:right-4 ${estilo}`}
      role="alert"
    >
      <span>{message}</span>
      <button
        onClick={onDismiss}
        className="shrink-0 text-current opacity-70 hover:opacity-100"
        aria-label="Fechar"
      >
        ✕
      </button>
    </div>
  );
}
