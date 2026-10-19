import './style.css';
export const metadata = { title: 'Print view · Centauri Carbon 2', description: 'Local printer monitor' };
export default function Layout({children}:Readonly<{children:React.ReactNode}>){return <html lang="en"><body>{children}</body></html>}
