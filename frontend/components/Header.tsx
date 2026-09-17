"use client";

import Link from "next/link";
import { BrandMark } from "./BrandMark";

export function Header() {
  return (
    <header className="site-header">
      <div className="header-inner">
        <Link className="brand" href="/" aria-label="สานศัพท์ หน้าหลัก">
          <BrandMark />
          <span>สานศัพท์</span>
        </Link>
        <div className="header-actions">
          <div className="runtime-status" aria-label="ระบบที่ใช้ตอบ">
            <i aria-hidden="true" />
            GraphDB · LLM ไม่บังคับ
          </div>
        </div>
      </div>
    </header>
  );
}
