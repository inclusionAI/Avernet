import React from "react";
import { Outlet } from '@umijs/max';

export default function BcnContainer() {
  return (
    <div className="h-[100vh] bg-slate-100 p-2">
      <Outlet />
    </div>
  );
}