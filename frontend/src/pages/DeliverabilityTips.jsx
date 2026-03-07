import { useState } from 'react';
import { Card } from '../components/ui/Card';
import {
  RiShieldCheckLine,
  RiMailSendLine,
  RiFileTextLine,
  RiAlertLine,
  RiCheckLine,
  RiArrowDownSLine,
} from 'react-icons/ri';

const sections = [
  {
    id: 'setup',
    label: 'Before You Send',
    icon: RiShieldCheckLine,
    rules: [
      {
        title: 'Verify your sending domain',
        body: 'Your domain needs four authentication records so mail servers trust your emails. SPF and DKIM are required.',
        tag: 'Required',
      },
      {
        title: 'Use a separate domain for cold outreach',
        body: 'Never send cold emails from your main business domain. Use a separate domain or subdomain just for outreach. If something goes wrong — high bounces, spam complaints — only that domain takes the hit. Your main domain stays clean.',
        tag: 'Required',
      },
      {
        title: 'Warm up new inboxes',
        body: 'Any new inbox must be warmed up before sending to real leads. Go to your inbox settings, open the inbox you want, and enable the warm-up toggle. Warming up gradually builds your sending reputation over 2-4 weeks. Skipping this is the most common reason emails land in spam.',
        tag: 'Required',
      },
      {
        title: 'Verify your contact list',
        body: 'Always verify your list before launching a campaign. Go to Settings → Optional Features and enable Email Verification. You can connect MailTester Ninja — it\'s cheap (under $20) and gives you a very high verification limit — or set up a custom provider. Once enabled, new leads added to any campaign are verified automatically.',
        tag: 'Recommended',
      },
    ],
  },
  {
    id: 'writing',
    label: 'Writing Emails',
    icon: RiFileTextLine,
    rules: [
      {
        title: 'Plain text vs HTML — what you actually need',
        body: 'If you are not tracking opens or clicks, send plain text. It looks personal, performs better, and avoids spam filters entirely. If you want to track opens or clicks, your email must be sent as HTML — that\'s what makes tracking technically possible. But if you\'re just starting out or sending low volume, plain text and tracking reply rates is more than enough. Compare your reply rate to industry benchmarks and go from there.',
        tag: null,
      },
      {
        title: 'If you do use tracking, keep the first email plain',
        body: 'If you want open or click tracking but still want the best deliverability, send the first email in every sequence as plain text with no tracking. Follow-ups can be HTML with tracking enabled. The first email is what builds the thread — getting that one into the inbox is what matters most.',
        tag: 'Tip',
      },
      {
        title: 'Write like a human',
        body: 'Short sentences. Direct point. No buzzwords, no "I hope this email finds you well." Write the way you\'d talk to someone in person. Long paragraphs and formal language get skimmed or ignored.',
        tag: null,
      },
      {
        title: 'Personalize beyond the first name',
        body: 'Everyone uses first name. What actually gets replies is something specific — a detail about their business, a recent post they wrote, a challenge their industry faces. The more relevant your email feels to that one person, the better.',
        tag: null,
      },
      {
        title: 'One ask per email',
        body: 'End every email with a single, low-commitment request. Asking someone to book a call, check your site, and reply to a question all at once reduces the chance they do any of it. Pick one.',
        tag: null,
      },
      {
        title: 'No attachments',
        body: 'Attachments on cold emails are a near-guaranteed spam trigger. If you need to share something, link to it.',
        tag: null,
      },
    ],
  },
  {
    id: 'sending',
    label: 'Sending Smart',
    icon: RiMailSendLine,
    rules: [
      {
        title: 'Send during recipient business hours',
        body: 'Send emails when your recipient is likely at their desk, not when it\'s convenient for you. An email that arrives at 3am sits under a pile of others by the time they wake up.',
        tag: null,
      },
      {
        title: '50 emails per inbox per day maximum',
        body: 'Even with a fully warmed inbox, cap at 50 emails per day per mailbox. If you need more volume, add more inboxes rather than pushing one past its limit.',
        tag: null,
      },
      {
        title: 'Space out your sends',
        body: 'Never blast your entire list at once. Spread emails throughout the day with random delays in between. Sending 500 emails in 10 minutes looks like automated bulk mail. Gradual sending looks human.',
        tag: null,
      },
      {
        title: 'Keep sequences to 2-3 emails',
        body: 'More follow-ups do not mean more replies. Past the third email, response rates drop and spam complaint rates rise. Respect people\'s time.',
        tag: null,
      },
    ],
  },
  {
    id: 'reputation',
    label: 'Protecting Your Reputation',
    icon: RiAlertLine,
    rules: [
      {
        title: 'Remove bounced addresses immediately',
        body: 'When an address bounces, it means it doesn\'t exist or can\'t receive mail. Remove it and never email it again. Keep your bounce rate under 2% — going above this triggers throttling and blocks from major mail providers. handled automatically if you enable email verification, but if you don\'t, make sure to check your bounce reports after every campaign and remove any bad addresses.',
        tag: 'Critical',
      },
      {
        title: 'Honor every unsubscribe',
        body: 'Anyone who asks to stop receiving emails — whether they click unsubscribe or reply with "stop" or "remove me" — must be permanently removed from all campaigns. This is both a spam trigger and a legal requirement under CAN-SPAM, GDPR, and most other email laws. No exceptions. handled automatically if you have email classification enabled and use unsubscribe headers and links in the email.',
        tag: 'Critical',
      },
      {
        title: 'Track replies, not opens',
        body: 'Reply rate is a real signal — someone actually responded. Open rates are unreliable because many apps count an open the moment an email arrives. Use reply rate as your main performance metric and compare it to industry benchmarks.',
        tag: null,
      },
    ],
  },
];

const tagStyles = {
  Required:    'bg-red-100 text-red-700 border border-red-200',
  Critical:    'bg-red-100 text-red-700 border border-red-200',
  Recommended: 'bg-amber-100 text-amber-700 border border-amber-200',
  Tip:         'bg-teal-100 text-teal-700 border border-teal-200',
};

const metrics = [
  { label: 'Bounce rate',             safe: 'Under 2%',   danger: 'Above 3%' },
  { label: 'Spam complaint rate',     safe: 'Under 0.1%', danger: 'Above 0.3%' },
  { label: 'Emails per inbox / day',  safe: 'Up to 50',   danger: 'Above 50' },
  { label: 'Follow-ups per sequence', safe: '2-3 emails', danger: '4+ emails' },
  { label: 'Warm-up period',          safe: '2-4 weeks',  danger: 'Skipping it' },
];

function Tag({ label }) {
  const cls = tagStyles[label];
  if (!cls) return null;
  return (
    <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded whitespace-nowrap flex-shrink-0 ${cls}`}>
      {label}
    </span>
  );
}

function Rule({ rule, index }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      onClick={() => setOpen(v => !v)}
      className={`rounded-lg border shadow-sm cursor-pointer transition-colors duration-150 ${
        open
          ? 'bg-gray-50 border-gray-300'
          : 'bg-white border-gray-200 hover:border-gray-300 hover:bg-gray-50'
      }`}
    >
      {/* Header row — never compresses */}
      <div className="flex items-center gap-3 px-5 py-4">
        <span className="text-xs font-semibold text-gray-300 w-6 flex-shrink-0 text-right tabular-nums">
          {String(index + 1).padStart(2, '0')}
        </span>
        <div className="flex-1 flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-sm font-medium text-gray-800 leading-snug">{rule.title}</span>
          {rule.tag && <Tag label={rule.tag} />}
        </div>
        <span className={`text-gray-400 flex-shrink-0 transition-transform duration-300 ${open ? 'rotate-180' : 'rotate-0'}`}>
          <RiArrowDownSLine size={18} />
        </span>
      </div>

      {/* Animated body — grid trick for smooth height transition */}
      <div className={`grid transition-all duration-300 ease-in-out ${open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
        <div className="overflow-hidden">
          <p className="px-5 py-4 pl-14 text-sm text-gray-600 leading-relaxed border-t border-gray-100">
            {rule.body}
          </p>
        </div>
      </div>
    </div>
  );
}

export default function DeliverabilityTips() {
  const [activeSection, setActiveSection] = useState('setup');
  const section = sections.find(s => s.id === activeSection);

  const sectionOffsets = {};
  let globalIndex = 0;
  sections.forEach(s => {
    sectionOffsets[s.id] = globalIndex;
    globalIndex += s.rules.length;
  });

  return (
    <div className="p-8 max-w-5xl mx-auto space-y-8">

      {/* Page header */}
      <div>
        <h1 className="text-2xl font-semibold text-gray-800 mb-1">Deliverability Tips</h1>
        <p className="text-sm text-gray-500">
          Best practices for landing in the inbox, getting read, and protecting your sender reputation.
        </p>
      </div>

      {/* Section tabs */}
      <div className="flex flex-wrap gap-2">
        {sections.map(s => {
          const Icon = s.icon;
          const active = activeSection === s.id;
          return (
            <button
              key={s.id}
              onClick={() => setActiveSection(s.id)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium border transition-colors duration-150 ${
                active
                  ? 'bg-primary/10 text-primary border-primary/30'
                  : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50 hover:text-gray-800'
              }`}
            >
              <Icon size={15} />
              {s.label}
            </button>
          );
        })}
      </div>

      {/* Rules list */}
      <div className="space-y-2">
        {section.rules.map((rule, i) => (
          <Rule key={rule.title} rule={rule} index={sectionOffsets[activeSection] + i} />
        ))}
      </div>

      {/* Quick reference table */}
      <div>
        <h2 className="text-base font-semibold text-gray-700 mb-3">Quick Reference</h2>
        <div className="rounded-lg border border-gray-200 overflow-hidden">
          <div className="grid grid-cols-3 bg-gray-50 px-4 py-2.5 border-b border-gray-200">
            {['Metric', 'Safe zone', 'Danger zone'].map(h => (
              <span key={h} className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{h}</span>
            ))}
          </div>
          {metrics.map((m, i) => (
            <div
              key={m.label}
              className={`grid grid-cols-3 px-4 py-3 text-sm ${
                i < metrics.length - 1 ? 'border-b border-gray-100' : ''
              } ${i % 2 === 1 ? 'bg-gray-50/50' : 'bg-white'}`}
            >
              <span className="text-gray-600 font-medium">{m.label}</span>
              <span className="flex items-center gap-1 text-teal-600 font-medium">
                <RiCheckLine size={14} className="flex-shrink-0" />{m.safe}
              </span>
              <span className="flex items-center gap-1 text-red-500 font-medium">
                <RiAlertLine size={14} className="flex-shrink-0" />{m.danger}
              </span>
            </div>
          ))}
        </div>
      </div>

    </div>
  );
}
