import Link from "next/link";

export default function WelcomePage() {
  return (
    <main className="min-h-screen bg-white text-gray-900">
      {/* Nav */}
      <nav className="mx-auto flex max-w-5xl items-center justify-between px-6 py-6">
        <span className="text-xl font-bold bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 bg-clip-text text-transparent">
          AmplifyMe
        </span>
        <div className="flex items-center gap-6 text-sm">
          <Link href="/privacy" className="text-gray-500 hover:text-gray-900">Privacy</Link>
          <Link href="/terms" className="text-gray-500 hover:text-gray-900">Terms</Link>
          <Link
            href="/login"
            className="rounded-lg bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-700 transition-colors"
          >
            Sign In
          </Link>
        </div>
      </nav>

      {/* Hero */}
      <section className="mx-auto max-w-5xl px-6 pt-20 pb-16 text-center">
        <h1 className="text-5xl font-bold leading-tight tracking-tight sm:text-6xl">
          AI-powered social media
          <br />
          <span className="bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 bg-clip-text text-transparent">
            for music artists
          </span>
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-lg text-gray-500">
          Manage your releases, schedule posts across Instagram, TikTok, and YouTube,
          and let AI help you build smarter campaigns — all from one place.
        </p>
        <div className="mt-10 flex justify-center gap-4">
          <Link
            href="/login"
            className="rounded-lg bg-indigo-600 px-6 py-3 font-semibold text-white hover:bg-indigo-700 transition-colors"
          >
            Get Started
          </Link>
        </div>
      </section>

      {/* Features */}
      <section className="mx-auto max-w-5xl px-6 py-20">
        <div className="grid grid-cols-1 gap-8 sm:grid-cols-3">
          <div className="rounded-2xl border border-gray-100 bg-gray-50 p-6">
            <div className="text-2xl">🎵</div>
            <h3 className="mt-3 text-lg font-semibold">Release Management</h3>
            <p className="mt-2 text-sm text-gray-500">
              Upload albums and singles with track-level audio files. Bulk upload
              entire catalogs with automatic metadata parsing.
            </p>
          </div>
          <div className="rounded-2xl border border-gray-100 bg-gray-50 p-6">
            <div className="text-2xl">📱</div>
            <h3 className="mt-3 text-lg font-semibold">Multi-Platform Publishing</h3>
            <p className="mt-2 text-sm text-gray-500">
              Publish to Instagram, TikTok, and YouTube from a single dashboard.
              Schedule posts for optimal times or publish instantly.
            </p>
          </div>
          <div className="rounded-2xl border border-gray-100 bg-gray-50 p-6">
            <div className="text-2xl">🤖</div>
            <h3 className="mt-3 text-lg font-semibold">AI Campaign Engine</h3>
            <p className="mt-2 text-sm text-gray-500">
              Generate content calendars, captions, and campaign strategies
              powered by AI that learns from your audience engagement.
            </p>
          </div>
        </div>
      </section>

      {/* How it works */}
      <section className="mx-auto max-w-5xl px-6 py-16">
        <h2 className="text-center text-3xl font-bold">How It Works</h2>
        <div className="mt-12 grid grid-cols-1 gap-6 sm:grid-cols-4">
          {[
            { step: "1", title: "Connect", desc: "Link your Instagram, YouTube, and TikTok accounts" },
            { step: "2", title: "Upload", desc: "Add your releases, tracks, and media content" },
            { step: "3", title: "Create", desc: "Write posts or let AI generate content for you" },
            { step: "4", title: "Publish", desc: "Schedule or publish instantly across all platforms" },
          ].map((item) => (
            <div key={item.step} className="text-center">
              <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-indigo-100 text-sm font-bold text-indigo-600">
                {item.step}
              </div>
              <h3 className="mt-3 font-semibold">{item.title}</h3>
              <p className="mt-1 text-sm text-gray-500">{item.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="mx-auto max-w-5xl px-6 py-20 text-center">
        <h2 className="text-3xl font-bold">Ready to amplify your music?</h2>
        <p className="mt-4 text-gray-500">
          Join artists using AmplifyMe to grow their audience across every platform.
        </p>
        <Link
          href="/login"
          className="mt-8 inline-block rounded-lg bg-indigo-600 px-8 py-3 font-semibold text-white hover:bg-indigo-700 transition-colors"
        >
          Get Started Free
        </Link>
      </section>

      {/* Footer */}
      <footer className="border-t border-gray-100 py-8">
        <div className="mx-auto max-w-5xl px-6 flex items-center justify-between text-sm text-gray-400">
          <span>&copy; {new Date().getFullYear()} AmplifyMe</span>
          <div className="flex gap-6">
            <Link href="/privacy" className="hover:text-gray-600">Privacy Policy</Link>
            <Link href="/terms" className="hover:text-gray-600">Terms of Service</Link>
          </div>
        </div>
      </footer>
    </main>
  );
}
