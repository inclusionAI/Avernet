import { Toaster as Sonner } from 'sonner';

export function Toaster() {
  return (
    <Sonner
      position="top-center"
      gap={8}
      toastOptions={{
        classNames: {
          toast:
            'rounded-xl border shadow-md text-[13px] font-normal backdrop-blur-sm',
          title: 'font-medium text-[13px]',
          description: 'text-[12px] mt-0.5 opacity-80',
          closeButton:
            'opacity-50 hover:opacity-100 transition-opacity !left-auto !right-0 !top-0',
          success:
            'border-green-200/80 bg-green-50 text-green-800 [&>[data-icon]]:text-green-500',
          error:
            'border-red-200/80 bg-red-50 text-red-800 [&>[data-icon]]:text-red-500',
          warning:
            'border-amber-200/80 bg-amber-50 text-amber-800 [&>[data-icon]]:text-amber-500',
          info: 'border-lavender-200/80 bg-lavender-50 text-lavender-800 [&>[data-icon]]:text-lavender-500',
        },
      }}
    />
  );
}
