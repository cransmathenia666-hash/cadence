"use client";

import "../cadence-theme.css";
import { WorkbenchView } from "@/components/workbench/workbench-view";

export default function WorkbenchPage() {
  return (
    <div className="h-screen w-full flex overflow-hidden selection:bg-white/20 dark">
      <WorkbenchView />
    </div>
  );
}
