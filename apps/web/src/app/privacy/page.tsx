export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-2xl px-6 py-16 text-[var(--text-primary)]">
      <h1 className="text-2xl font-bold">Privacy Policy</h1>
      <p className="mt-4 text-sm text-[var(--text-secondary)]">Last updated: March 23, 2026</p>
      <div className="mt-6 space-y-4 text-sm leading-relaxed text-[var(--text-secondary)]">
        <p>Amplify-OS (&quot;we&quot;, &quot;our&quot;, &quot;us&quot;) respects your privacy. This policy describes how we collect, use, and protect your information.</p>
        <h2 className="text-lg font-semibold text-[var(--text-primary)]">Information We Collect</h2>
        <p>We collect information you provide when creating an account (email, name) and data from third-party platforms you connect (Instagram, YouTube, TikTok) to manage your content.</p>
        <h2 className="text-lg font-semibold text-[var(--text-primary)]">How We Use Your Information</h2>
        <p>We use your information solely to provide the Amplify-OS service — publishing content, managing campaigns, and displaying analytics from your connected accounts.</p>
        <h2 className="text-lg font-semibold text-[var(--text-primary)]">Data Sharing</h2>
        <p>We do not sell your personal information. We only share data with third-party platforms as necessary to provide the service (e.g., posting content you approve).</p>
        <h2 className="text-lg font-semibold text-[var(--text-primary)]">Data Security</h2>
        <p>We encrypt OAuth tokens at rest and use HTTPS for all communications. Access to your connected accounts can be revoked at any time from your settings.</p>
        <h2 className="text-lg font-semibold text-[var(--text-primary)]">Contact</h2>
        <p>For privacy questions, contact us at support@amplify-os.com.</p>
      </div>
    </main>
  );
}
