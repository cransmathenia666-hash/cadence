"use client";
// beui.dev/components/motion/tilt-card
// 悬停时卡片随光标做 3D 倾斜，附一圈跟手的微光。触屏与 reduce-motion 下自动关闭。

import {
  motion,
  useMotionTemplate,
  useMotionValue,
  useReducedMotion,
  useSpring,
} from "motion/react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { SPRING_MOUSE } from "@/lib/ease";
import { cn } from "@/lib/utils";

function useHoverCapable() {
  const [capable, setCapable] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(hover: hover) and (pointer: fine)");
    const update = () => setCapable(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return capable;
}

export interface TiltCardProps {
  children?: ReactNode;
  /** 最大倾角（度）。卡片类内容建议 4–6，海报类可到 12。 */
  max?: number;
  /** 是否渲染跟随光标的微光。 */
  glare?: boolean;
  className?: string;
}

export function TiltCard({
  children,
  max = 12,
  glare = true,
  className,
}: TiltCardProps) {
  const reduce = useReducedMotion() ?? false;
  const canHover = useHoverCapable();
  const enabled = !reduce && canHover;
  const frameRef = useRef<HTMLDivElement>(null);

  const rotateX = useMotionValue(0);
  const rotateY = useMotionValue(0);
  const glareX = useMotionValue(50);
  const glareY = useMotionValue(50);
  const springX = useSpring(rotateX, SPRING_MOUSE);
  const springY = useSpring(rotateY, SPRING_MOUSE);
  const springGlareX = useSpring(glareX, SPRING_MOUSE);
  const springGlareY = useSpring(glareY, SPRING_MOUSE);

  const transform = useMotionTemplate`perspective(1000px) rotateX(${springX}deg) rotateY(${springY}deg)`;
  const glareBackground = useMotionTemplate`radial-gradient(circle at ${springGlareX}% ${springGlareY}%, var(--foreground), transparent 55%)`;

  const handleMove = (event: React.MouseEvent<HTMLDivElement>) => {
    const frame = frameRef.current;
    if (!frame || !enabled) return;
    const rect = frame.getBoundingClientRect();
    const px = (event.clientX - rect.left) / rect.width;
    const py = (event.clientY - rect.top) / rect.height;
    rotateY.set((px - 0.5) * max);
    rotateX.set((0.5 - py) * max);
    glareX.set(px * 100);
    glareY.set(py * 100);
  };

  const handleLeave = () => {
    rotateX.set(0);
    rotateY.set(0);
    glareX.set(50);
    glareY.set(50);
  };

  return (
    <motion.div
      ref={frameRef}
      onMouseMove={handleMove}
      onMouseLeave={handleLeave}
      style={{
        transform,
        transformStyle: "preserve-3d",
      }}
      className={cn("relative", className)}
    >
      {children}
      {glare && enabled ? (
        <motion.div
          aria-hidden="true"
          style={{ background: glareBackground }}
          className="pointer-events-none absolute inset-0 rounded-[inherit] opacity-[0.12]"
        />
      ) : null}
    </motion.div>
  );
}
