export function CardSkeleton() {
  return (
    <div className="flex animate-pulse items-center gap-3 rounded-xl border border-border bg-surface p-4">
      <div className="h-[52px] w-[52px] shrink-0 rounded-full bg-border" />
      <div className="flex-1 space-y-2">
        <div className="h-4 w-1/2 rounded bg-border" />
        <div className="h-3 w-2/3 rounded bg-border" />
      </div>
      <div className="h-8 w-24 shrink-0 rounded-md bg-border" />
    </div>
  );
}
