import { useState, useEffect } from 'react';
import { useOnboarding } from '../context/OnboardingContext';
import {
  RiMailLine,
  RiSendPlaneLine,
  RiUserLine,
  RiCalendarScheduleLine,
  RiBarChartLine,
  RiShieldCheckLine,
  RiRocketLine,
  RiArrowLeftLine,
  RiArrowRightLine,
  RiCloseLine,
  RiCheckLine,
} from 'react-icons/ri';
import logo from '../assets/quickly_logo.svg';

/* ── step definitions ─────────────────────────────────────────────────────── */
const steps = [
  {
    id: 'welcome',
    icon: <RiRocketLine size={40} />,
    title: 'Welcome to Quickly',
    subtitle: 'Your email outreach platform',
    description:
      'Quickly helps you send personalized cold email campaigns at scale using your Gmail accounts. ' +
      'This quick tour will walk you through the key features so you can hit the ground running.',
    highlights: [
      'Send multi-step email sequences automatically',
      'Track opens, clicks, and replies in real-time',
      'Manage multiple Gmail accounts with smart scheduling',
      'A/B test your email copy for better results',
    ],
    color: 'from-teal-500 to-emerald-500',
  },
  {
    id: 'inboxes',
    icon: <RiMailLine size={40} />,
    title: 'Connect Your Inboxes',
    subtitle: 'Gmail account management',
    description:
      'Start by connecting one or more Gmail accounts. Each inbox has its own daily sending limit ' +
      'and can be shared across multiple campaigns.',
    highlights: [
      'Connect unlimited Gmail accounts via OAuth',
      'Set daily sending limits per inbox (e.g. 50 emails/day)',
      'Enable ramp-up mode to gradually warm up new accounts',
      'Configure wait time between emails for natural sending',
      'Optionally set custom tracking domains per inbox',
    ],
    color: 'from-blue-500 to-cyan-500',
    tip: 'Start with low daily limits (10-20) and ramp up gradually for best deliverability.',
  },
  {
    id: 'campaigns',
    icon: <RiSendPlaneLine size={40} />,
    title: 'Create Campaigns',
    subtitle: 'Multi-step email sequences',
    description:
      'Campaigns are the core of Quickly. Each campaign contains a sequence of emails that are ' +
      'sent automatically at intervals you define.',
    highlights: [
      'Build multi-step sequences (initial email + follow-ups)',
      'Set wait days between each step (e.g. 3 days between emails)',
      'Create A/B variants to test different subject lines and copy',
      'Choose sending windows (days of week + time range)',
      'Use template variables like {{name}}, {{company}}, {{custom fields}}',
    ],
    color: 'from-violet-500 to-purple-500',
    tip: 'Keep sequences to 3-4 steps max. Most replies come from the first two emails.',
  },
  {
    id: 'leads',
    icon: <RiUserLine size={40} />,
    title: 'Add Your Leads',
    subtitle: 'Lead management & enrollment',
    description:
      'Add leads to your campaigns individually or in bulk. Each lead can carry custom data ' +
      'that gets injected into your email templates.',
    highlights: [
      'Import leads with email, name, company, and custom fields',
      'Bulk enroll hundreds of leads at once',
      'Template variables auto-populate from lead data',
      'Track lead status: active, replied, bounced, unsubscribed',
      'Optional email verification before sending',
    ],
    color: 'from-amber-500 to-orange-500',
    tip: 'Always verify email addresses before sending to keep your bounce rate low.',
  },
  {
    id: 'scheduling',
    icon: <RiCalendarScheduleLine size={40} />,
    title: 'Smart Scheduling',
    subtitle: 'Queue engine & strategies',
    description:
      'Quickly\'s scheduling engine automatically queues emails based on your inbox limits, ' +
      'campaign settings, and sending windows. No manual scheduling needed.',
    highlights: [
      'Priority mode — drag campaigns to set send order',
      'Round-robin mode — distribute sends evenly across campaigns',
      'Respects inbox daily limits and ramp-up schedules',
      'Timezone-aware scheduling (send in recipients\' local time)',
      'View all upcoming sends in the Schedule page',
    ],
    color: 'from-pink-500 to-rose-500',
    tip: 'Use Priority mode when you have a high-priority campaign that should be sent first.',
  },
  {
    id: 'tracking',
    icon: <RiShieldCheckLine size={40} />,
    title: 'Tracking & Deliverability',
    subtitle: 'Monitor engagement safely',
    description:
      'Track how recipients engage with your emails. Open and click tracking can be toggled ' +
      'per campaign. Follow deliverability best practices to stay out of spam.',
    highlights: [
      'Optional open and click tracking per campaign',
      'Custom tracking domains for better deliverability',
      'Automatic unsubscribe links in every email',
      'Known IP filtering — exclude your own opens/clicks',
      'Deliverability Tips page with best practices',
    ],
    color: 'from-green-500 to-teal-500',
    tip: 'Consider disabling open tracking for cold outreach — it can hurt deliverability.',
  },
  {
    id: 'analytics',
    icon: <RiBarChartLine size={40} />,
    title: 'Analytics, Unibox & More',
    subtitle: 'Monitor, reply, and configure',
    description:
      'Track campaign performance in Analytics, manage replies in the Unified Inbox, ' +
      'and fine-tune your setup in Settings.',
    highlights: [
      'Analytics — timeline charts for sends, opens, clicks, and replies',
      'Unibox — unified inbox syncs all Gmail replies in one place',
      'Webhooks — get notified on email events (sent, opened, replied, etc.)',
      'Test Mode — simulate everything without sending real emails',
      'AI Features — classify lead interest with AI providers',
    ],
    color: 'from-indigo-500 to-blue-500',
    tip: 'Enable Test Mode in Settings to safely try the full workflow before going live.',
  },
];

/* ── step card component ──────────────────────────────────────────────────── */
function StepCard({ step, isActive }) {
  return (
    <div
      className={`transition-all duration-500 ${
        isActive ? 'opacity-100 translate-x-0' : 'opacity-0 absolute pointer-events-none'
      }`}
    >
      {/* icon + gradient header */}
      <div className={`bg-gradient-to-br ${step.color} rounded-2xl p-6 mb-6 flex items-center gap-4 text-white shadow-lg`}>
        <div className="bg-white/20 rounded-xl p-3 backdrop-blur-sm">
          {step.icon}
        </div>
        <div>
          <h2 className="text-2xl font-bold leading-tight">{step.title}</h2>
          <p className="text-white/80 text-sm mt-0.5">{step.subtitle}</p>
        </div>
      </div>

      {/* description */}
      <p className="text-gray-600 leading-relaxed mb-5">{step.description}</p>

      {/* highlights */}
      <ul className="space-y-2.5 mb-5">
        {step.highlights.map((h, i) => (
          <li key={i} className="flex items-start gap-2.5">
            <span className={`mt-0.5 flex-shrink-0 h-5 w-5 rounded-full bg-gradient-to-br ${step.color} flex items-center justify-center`}>
              <RiCheckLine size={12} className="text-white" />
            </span>
            <span className="text-gray-700 text-sm leading-relaxed">{h}</span>
          </li>
        ))}
      </ul>

      {/* tip */}
      {step.tip && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg px-4 py-3 text-sm text-amber-800">
          <span className="font-semibold">Tip:</span> {step.tip}
        </div>
      )}
    </div>
  );
}

/* ── progress dots ────────────────────────────────────────────────────────── */
function StepDots({ current, total, onDotClick }) {
  return (
    <div className="flex items-center gap-2">
      {Array.from({ length: total }, (_, i) => (
        <button
          key={i}
          onClick={() => onDotClick(i)}
          className={`rounded-full transition-all duration-300 ${
            i === current
              ? 'w-8 h-2.5 bg-primary'
              : i < current
                ? 'w-2.5 h-2.5 bg-primary/40 hover:bg-primary/60'
                : 'w-2.5 h-2.5 bg-gray-300 hover:bg-gray-400'
          }`}
          aria-label={`Go to step ${i + 1}`}
        />
      ))}
    </div>
  );
}

/* ── main onboarding overlay ──────────────────────────────────────────────── */
export default function Onboarding() {
  const { showOnboarding, completeOnboarding } = useOnboarding();
  const [current, setCurrent] = useState(0);
  const [animDir, setAnimDir] = useState('right'); // for future animation direction

  // reset step when reopened
  useEffect(() => {
    if (showOnboarding) setCurrent(0);
  }, [showOnboarding]);

  if (!showOnboarding) return null;

  const isFirst = current === 0;
  const isLast = current === steps.length - 1;

  const goNext = () => {
    if (isLast) {
      completeOnboarding();
    } else {
      setAnimDir('right');
      setCurrent(c => c + 1);
    }
  };

  const goPrev = () => {
    if (!isFirst) {
      setAnimDir('left');
      setCurrent(c => c - 1);
    }
  };

  const goTo = (i) => {
    setAnimDir(i > current ? 'right' : 'left');
    setCurrent(i);
  };

  // keyboard navigation
  const handleKeyDown = (e) => {
    if (e.key === 'ArrowRight' || e.key === 'Enter') goNext();
    else if (e.key === 'ArrowLeft') goPrev();
    else if (e.key === 'Escape') completeOnboarding();
  };

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-gray-900/60 backdrop-blur-sm"
      onKeyDown={handleKeyDown}
      tabIndex={0}
      role="dialog"
      aria-modal="true"
      aria-label="Onboarding wizard"
    >
      <div className="relative w-full max-w-lg mx-4 bg-white rounded-2xl shadow-2xl overflow-hidden animate-in">
        {/* skip button */}
        <button
          onClick={completeOnboarding}
          className="absolute top-4 right-4 z-10 text-gray-400 hover:text-gray-600 transition-colors rounded-full p-1 hover:bg-gray-100"
          aria-label="Skip onboarding"
        >
          <RiCloseLine size={20} />
        </button>

        {/* content area */}
        <div className="p-8 pb-4 min-h-[420px] relative">
          {/* logo watermark for welcome step */}
          {current === 0 && (
            <div className="flex justify-center mb-4">
              <img src={logo} alt="Quickly" className="h-12 w-12 opacity-80" />
            </div>
          )}

          {/* step counter */}
          <div className="text-xs text-gray-400 font-medium mb-4 uppercase tracking-wider">
            Step {current + 1} of {steps.length}
          </div>

          {/* step content */}
          <div className="relative">
            {steps.map((step, i) => (
              <StepCard key={step.id} step={step} isActive={i === current} />
            ))}
          </div>
        </div>

        {/* footer — dots + nav buttons */}
        <div className="px-8 py-5 bg-gray-50 flex items-center justify-between border-t border-gray-100">
          <StepDots current={current} total={steps.length} onDotClick={goTo} />

          <div className="flex items-center gap-2">
            {!isFirst && (
              <button
                onClick={goPrev}
                className="flex items-center gap-1 px-3 py-2 text-sm text-gray-600 hover:text-gray-800 hover:bg-gray-200 rounded-lg transition-colors"
              >
                <RiArrowLeftLine size={16} />
                Back
              </button>
            )}
            {isFirst && (
              <button
                onClick={completeOnboarding}
                className="px-3 py-2 text-sm text-gray-500 hover:text-gray-700 hover:bg-gray-200 rounded-lg transition-colors"
              >
                Skip tour
              </button>
            )}
            <button
              onClick={goNext}
              className="flex items-center gap-1.5 px-5 py-2.5 bg-primary text-white rounded-lg text-sm font-semibold hover:bg-primary/90 transition-colors shadow-sm"
            >
              {isLast ? (
                <>
                  Get Started
                  <RiRocketLine size={16} />
                </>
              ) : (
                <>
                  Next
                  <RiArrowRightLine size={16} />
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
