import type { Metadata } from "next";
import "@fontsource-variable/noto-sans-thai";
import "./globals.css";
import { Header } from "@/components/Header";

export const metadata: Metadata = {
  title: "สานศัพท์ — คลังความรู้คำศัพท์ภาษาไทย",
  description: "ค้นความหมายตามบริบท พร้อมหลักฐานและเส้นทางในกราฟความรู้คำศัพท์ภาษาไทย",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="th">
      <body>
        <Header />
        {children}
      </body>
    </html>
  );
}
