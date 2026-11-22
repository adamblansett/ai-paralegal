import { AlertTriangle } from 'lucide-react';

interface DisclaimerBannerProps {
  text?: string;
}

export function DisclaimerBanner({ text }: DisclaimerBannerProps) {
  return (
    <div className="flex items-start gap-2.5 border-b border-amber-200 bg-amber-50/90 px-6 py-2.5 text-xs text-amber-950">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
      <p className="leading-relaxed">
        <span className="font-semibold">Attorney Review Required (Not Legal Advice):</span>{' '}
        {text ||
          'AI-assisted legal operations work product. All extracted clauses, citations, risk flags, chronologies, and drafts must be independently verified by a licensed attorney before reliance.'}
      </p>
    </div>
  );
}
