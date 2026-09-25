export function Topbar({ planName }: { planName?: string }) {
  return (
    <header className="glass-header absolute top-0 left-0 right-0 h-14 flex items-center px-6 z-30 justify-between">
      <div className="flex items-center gap-3 text-sm font-medium">
        <span
          className="text-white opacity-90 tracking-wide font-semibold text-lg"
          style={{ fontFamily: "serif", letterSpacing: "-0.02em" }}
        >
          cadence
        </span>
        {planName && (
          <>
            <span className="text-white/20 mx-1">/</span>
            <span className="text-muted/80">{planName}</span>
          </>
        )}
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)] mt-1 animate-pulse"></span>
      </div>
    </header>
  );
}
