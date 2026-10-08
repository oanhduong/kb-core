/** @type {import('tailwindcss').Config} */
// Hệ màu "provenance": ink (điều hướng, tiêu đề) · paper (nền) · brand/signal (hành động) · marker (dạ quang — bằng chứng,
// trích dẫn, giá trị khớp nguồn) · verify (đã kiểm chứng) · alert. Chỉ marker được dùng "đậm" — phần còn lại tiết chế.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#F3F1FF", 100: "#E8E4FF", 200: "#D3CBFF", 300: "#B3A6FF", 400: "#8E7CFB",
          500: "#6F5CF8", 600: "#5B4CF5", 700: "#4A3BD6", 800: "#3D31AD", 900: "#332B88",
        },
        ink: { 950: "#14122F", 900: "#1E1B4B", 800: "#2A2766", 700: "#3A3680", 600: "#57538F", 400: "#8C89B8", 300: "#B9B6D9", 100: "#E7E6F3" },
        paper: "#F6F5FB",
        marker: { 50: "#FFFBEA", 100: "#FEF6C7", 200: "#FDE68A", 300: "#FCD34D", 700: "#8A5A0B" },
        verify: { 50: "#E8F7F1", 100: "#C9EEDF", 500: "#16B07E", 600: "#0E9F6E", 700: "#0B7F58" },
        alert: { 50: "#FDECEC", 500: "#E5484D", 700: "#B4282D" },
      },
      fontFamily: {
        sans: ["'Be Vietnam Pro'", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        display: ["'Bricolage Grotesque'", "'Be Vietnam Pro'", "ui-sans-serif", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        card: "0 1px 0 rgba(30,27,75,.04), 0 1px 3px rgba(30,27,75,.06)",
        lift: "0 12px 32px -12px rgba(30,27,75,.22)",
        focus: "0 0 0 3px rgba(91,76,245,.25)",
      },
      borderRadius: { xl: "14px", "2xl": "20px" },
      keyframes: {
        draw: { from: { strokeDashoffset: "var(--len, 400)" }, to: { strokeDashoffset: "0" } },
        rise: { from: { opacity: "0", transform: "translateY(6px)" }, to: { opacity: "1", transform: "none" } },
      },
      animation: { draw: "draw 1.2s cubic-bezier(.65,0,.35,1) forwards", rise: "rise .45s ease-out both" },
    },
  },
  plugins: [],
};
