// Weather condition icons, drawn here as inline SVG (64×64 box). The icon
// names are pfm.condition()'s; night swaps the sun for the moon. Colours
// are the --wx-* tokens in styles.css (with light-host overrides), so they
// follow an embedding host's scheme.

export type IconName = "clear" | "mostly-clear" | "partly-cloudy" | "mostly-cloudy" | "cloudy" | "fog" | "drizzle"
  | "showers" | "rain" | "tstorms" | "snow" | "snow-showers" | "mix" | "windy";

// A cloud 40 wide and 24 tall with a flat base, placed by translate/scale.
const CLOUD = "M8 24h24a8 8 0 0 0 .5-16a11 11 0 0 0-20.5-2a9 9 0 0 0-4 18z";

function Cloud({ x, y, s, dark }: { x: number; y: number; s: number; dark?: boolean }) {
  return (
    <path d={CLOUD} transform={`translate(${x} ${y}) scale(${s})`}
      fill={dark ? "var(--wx-cloud-dark)" : "var(--wx-cloud)"} />
  );
}

function Sun({ cx, cy, r }: { cx: number; cy: number; r: number }) {
  const rays = Array.from({ length: 8 }, (_, i) => {
    const a = (i * Math.PI) / 4;
    const [c, s] = [Math.cos(a), Math.sin(a)];
    return <line key={i} x1={cx + c * (r + 3.5)} y1={cy + s * (r + 3.5)} x2={cx + c * (r + 8)} y2={cy + s * (r + 8)} />;
  });
  return (
    <g>
      <g stroke="var(--wx-sun)" strokeWidth={3.2} strokeLinecap="round">{rays}</g>
      <circle cx={cx} cy={cy} r={r} fill="var(--wx-sun)" />
    </g>
  );
}

function Moon({ cx, cy, r }: { cx: number; cy: number; r: number }) {
  // Outer arc the long way round from upper right to lower right, inner arc back.
  const p1 = [cx + 0.5 * r, cy - 0.866 * r], p2 = [cx + 0.866 * r, cy + 0.5 * r];
  const d = `M${p1[0]} ${p1[1]}A${r} ${r} 0 1 0 ${p2[0]} ${p2[1]}A${r * 0.78} ${r * 0.78} 0 0 1 ${p1[0]} ${p1[1]}Z`;
  return <path d={d} fill="var(--wx-moon)" />;
}

const Orb = ({ night, cx, cy, r }: { night: boolean; cx: number; cy: number; r: number }) =>
  night ? <Moon cx={cx} cy={cy} r={r + 1} /> : <Sun cx={cx} cy={cy} r={r} />;

function Drops({ at, color = "var(--wx-rain)" }: { at: [number, number][]; color?: string }) {
  return (
    <g stroke={color} strokeWidth={3.6} strokeLinecap="round">
      {at.map(([x, y]) => <line key={`${x},${y}`} x1={x} y1={y} x2={x - 3.5} y2={y + 9} />)}
    </g>
  );
}

function Flakes({ at }: { at: [number, number][] }) {
  return (
    <g stroke="var(--wx-snow)" strokeWidth={2.4} strokeLinecap="round">
      {at.map(([x, y]) => (
        <g key={`${x},${y}`}>
          {[0, 60, 120].map((a) => {
            const r = (a * Math.PI) / 180, dx = Math.cos(r) * 4.5, dy = Math.sin(r) * 4.5;
            return <line key={a} x1={x - dx} y1={y - dy} x2={x + dx} y2={y + dy} />;
          })}
        </g>
      ))}
    </g>
  );
}

function body(icon: IconName, night: boolean) {
  switch (icon) {
    case "clear":
      return night ? <Moon cx={31} cy={32} r={17} /> : <Sun cx={32} cy={32} r={12} />;
    case "mostly-clear":
      return <><Orb night={night} cx={27} cy={27} r={12} /><Cloud x={30} y={37} s={0.68} /></>;
    case "partly-cloudy":
      return <><Orb night={night} cx={23} cy={23} r={10} /><Cloud x={14} y={28} s={0.95} /></>;
    case "mostly-cloudy":
      return <><Orb night={night} cx={19} cy={19} r={8} /><Cloud x={8} y={25} s={1.15} /></>;
    case "cloudy":
      return <><Cloud x={20} y={14} s={0.9} dark /><Cloud x={5} y={27} s={1.15} /></>;
    case "rain":
      return <><Cloud x={6} y={8} s={1.3} /><Drops at={[[21, 45], [33, 45], [45, 45]]} /></>;
    case "showers":
      return <><Orb night={night} cx={44} cy={17} r={8} /><Cloud x={4} y={13} s={1.2} /><Drops at={[[19, 49], [32, 49]]} /></>;
    case "drizzle":
      return (
        <><Cloud x={6} y={8} s={1.3} />
          <g fill="var(--wx-rain)">{[[18, 49], [28, 54], [38, 49], [47, 55], [23, 58]].map(([x, y]) =>
            <circle key={`${x},${y}`} cx={x} cy={y} r={2.3} />)}</g></>
      );
    case "tstorms":
      return (
        <><Cloud x={6} y={6} s={1.3} dark />
          <path d="M35 37L25 51h8l-4 11l14-17h-8l4-8z" fill="var(--wx-bolt)" />
          <Drops at={[[17, 44], [50, 44]]} /></>
      );
    case "snow":
      return <><Cloud x={6} y={8} s={1.3} /><Flakes at={[[18, 50], [32, 55], [46, 50]]} /></>;
    case "snow-showers":
      return <><Orb night={night} cx={44} cy={17} r={8} /><Cloud x={4} y={13} s={1.2} /><Flakes at={[[18, 52], [33, 54]]} /></>;
    case "mix":
      return <><Cloud x={6} y={8} s={1.3} /><Drops at={[[20, 45], [46, 45]]} /><Flakes at={[[31, 53]]} /></>;
    case "fog":
      return (
        <><Cloud x={10} y={6} s={1.1} />
          <g stroke="var(--wx-fog)" strokeWidth={4} strokeLinecap="round">
            <line x1={10} y1={42} x2={54} y2={42} /><line x1={16} y1={50} x2={48} y2={50} />
            <line x1={10} y1={58} x2={40} y2={58} /></g></>
      );
    case "windy":
      return (
        <g fill="none" stroke="var(--wx-cloud)" strokeWidth={4} strokeLinecap="round">
          <path d="M8 24h30a7 7 0 1 0-7-7" /><path d="M8 34h40a7 7 0 1 1-7 7" /><path d="M8 44h22" />
        </g>
      );
  }
}

export default function WxIcon({ icon, night = false, size = 48, title }: {
  icon: string; night?: boolean; size?: number; title?: string;
}) {
  const known = (body(icon as IconName, night) ?? body("cloudy", night));
  return (
    <svg className="wxicon" viewBox="0 0 64 64" width={size} height={size} role="img" aria-label={title}>
      {title && <title>{title}</title>}
      {known}
    </svg>
  );
}

export const ICONS: IconName[] = ["clear", "mostly-clear", "partly-cloudy", "mostly-cloudy", "cloudy", "fog", "drizzle",
  "showers", "rain", "tstorms", "snow", "snow-showers", "mix", "windy"];
