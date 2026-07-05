// Icon components live here so future skin/theme work can grow this file
// without adding more inline SVG noise to App.tsx. Intentionally minimal for now.

type IconProps = {
  className?: string
  size?: number
}

export function CloseIcon({ className, size = 16 }: IconProps) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M3 3L13 13M13 3L3 13"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function SpinnerIcon({ className, size = 16 }: IconProps) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <circle
        cx="8"
        cy="8"
        r="6.5"
        stroke="currentColor"
        strokeOpacity="0.25"
        strokeWidth="1.6"
      />
      <path
        d="M14.5 8a6.5 6.5 0 0 0-6.5-6.5"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  )
}

// The icons below share one visual language (SF Symbols-style line icons: 20x20 grid,
// 1.5px stroke, round caps/joins, no fill) so they read as one consistent system no
// matter where they're dropped in — buttons, links, badges, empty states.
function iconSvgProps(className: string | undefined, size: number) {
  return {
    className,
    width: size,
    height: size,
    viewBox: '0 0 20 20',
    fill: 'none' as const,
    'aria-hidden': true as const,
    focusable: 'false' as const,
  }
}

export function DownloadIcon({ className, size = 16 }: IconProps) {
  return (
    <svg {...iconSvgProps(className, size)}>
      <path d="M10 3v9m0 0-3.5-3.5M10 12l3.5-3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 14.5V16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-1.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function UploadIcon({ className, size = 16 }: IconProps) {
  return (
    <svg {...iconSvgProps(className, size)}>
      <path d="M10 12V3m0 0-3.5 3.5M10 3l3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 14.5V16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-1.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function ArrowRightIcon({ className, size = 16 }: IconProps) {
  return (
    <svg {...iconSvgProps(className, size)}>
      <path d="M4 10h12m0 0-4.5-4.5M16 10l-4.5 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function ArrowLeftIcon({ className, size = 16 }: IconProps) {
  return (
    <svg {...iconSvgProps(className, size)}>
      <path d="M16 10H4m0 0 4.5-4.5M4 10l4.5 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function CheckIcon({ className, size = 16 }: IconProps) {
  return (
    <svg {...iconSvgProps(className, size)}>
      <path d="M4 10.5l4 4 8-9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function DocumentIcon({ className, size = 16 }: IconProps) {
  return (
    <svg {...iconSvgProps(className, size)}>
      <path d="M6 2.5h5.5L15 6v11a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3.5a1 1 0 0 1 1-1Z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M11.5 2.5V6H15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M7 10h6M7 13h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function ClockIcon({ className, size = 16 }: IconProps) {
  return (
    <svg {...iconSvgProps(className, size)}>
      <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.5" />
      <path d="M10 6v4l3 2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function SearchIcon({ className, size = 16 }: IconProps) {
  return (
    <svg {...iconSvgProps(className, size)}>
      <circle cx="8.5" cy="8.5" r="5.5" stroke="currentColor" strokeWidth="1.5" />
      <path d="M16 16l-3.8-3.8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

export function WarningIcon({ className, size = 16 }: IconProps) {
  return (
    <svg {...iconSvgProps(className, size)}>
      <path d="M10 3.2 17.3 16H2.7L10 3.2Z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M10 8.3v3.4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="10" cy="14.1" r="0.9" fill="currentColor" stroke="none" />
    </svg>
  )
}

export function SparkleIcon({ className, size = 16 }: IconProps) {
  return (
    <svg {...iconSvgProps(className, size)}>
      <path
        d="M10 2.4c.32 2.55 1.03 4.16 2.28 5.4 1.25 1.25 2.86 1.96 5.4 2.28-2.54.32-4.15 1.03-5.4 2.28-1.25 1.24-1.96 2.85-2.28 5.4-.32-2.55-1.03-4.16-2.28-5.4-1.25-1.25-2.86-1.96-5.4-2.28 2.54-.32 4.15-1.03 5.4-2.28 1.25-1.24 1.96-2.85 2.28-5.4Z"
        fill="currentColor"
        stroke="none"
      />
    </svg>
  )
}

export function ChevronDownIcon({ className, size = 16 }: IconProps) {
  return (
    <svg {...iconSvgProps(className, size)}>
      <path d="M5 7.5l5 5 5-5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function ChevronRightIcon({ className, size = 16 }: IconProps) {
  return (
    <svg {...iconSvgProps(className, size)}>
      <path d="M7.5 5l5 5-5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function RefreshIcon({ className, size = 16 }: IconProps) {
  return (
    <svg {...iconSvgProps(className, size)}>
      <path d="M4 10a6 6 0 0 1 10.5-4M16 10a6 6 0 0 1-10.5 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M14 3v3h-3M6 17v-3h3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function UserIcon({ className, size = 16 }: IconProps) {
  return (
    <svg {...iconSvgProps(className, size)}>
      <circle cx="10" cy="7" r="3" stroke="currentColor" strokeWidth="1.5" />
      <path d="M3.5 17c1-3.3 3.7-5 6.5-5s5.5 1.7 6.5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

// Larger, more decorative than the toolbar-scale icons above (same line language, bigger
// canvas) — used inside EmptyState to give an otherwise blank state something to look at.
export function EmptyIllustration({ className, size = 72 }: IconProps) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 96 96"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="20" y="14" width="42" height="56" rx="4" stroke="currentColor" strokeWidth="2" opacity="0.55" />
      <path d="M28 30h26M28 40h26M28 50h16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" opacity="0.4" />
      <circle cx="63" cy="63" r="14" stroke="currentColor" strokeWidth="2.5" />
      <path d="M73 73l9 9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  )
}

export function BuildingIcon({ className, size = 16 }: IconProps) {
  return (
    <svg {...iconSvgProps(className, size)}>
      <path d="M4 17V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M14 17V8.5a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1V17" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M6.5 5.5h1.5M6.5 8.5h1.5M6.5 11.5h1.5M9.5 5.5H11M9.5 8.5H11M9.5 11.5H11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M3 17h14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}
