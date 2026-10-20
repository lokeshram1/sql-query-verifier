import './style.css';
import type { Metadata } from 'next';
export const metadata: Metadata = { title: 'SQL Query Verifier', description: 'Evidence before trust for generated SQL.' };
export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="en"><body>{children}</body></html>; }
