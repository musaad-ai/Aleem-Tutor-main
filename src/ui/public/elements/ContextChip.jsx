export default function ContextChip() {
  return (
    <div
      style={{
        position: "fixed",
        top: "12px",
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 40,
        display: "flex",
        alignItems: "center",
        gap: "8px",
        padding: "5px 14px",
        borderRadius: "9999px",
        fontSize: "13px",
        lineHeight: "1",
        fontWeight: 500,
        whiteSpace: "nowrap",
        pointerEvents: "none",
        color: "hsl(var(--foreground))",
        background: "hsl(var(--card) / 0.85)",
        border: "1px solid hsl(var(--border))",
        boxShadow: "0 1px 3px rgba(0,0,0,0.18)",
        backdropFilter: "blur(8px)",
        WebkitBackdropFilter: "blur(8px)",
      }}
    >
      <span style={{ opacity: 0.6 }}>الصف · Grade</span>
      <strong>{props.grade}</strong>
      <span style={{ opacity: 0.35 }}>•</span>
      <span>{props.subject}</span>
    </div>
  );
}
