-- Migration: Add approval/refusal metric types, log_hours, and edit/cancellation metrics to session_metrics
-- This allows tracking manager approval actions for timeoff and overtime requests,
-- tracking when users log their hours, and tracking edit/cancellation actions

-- Drop the existing check constraint
ALTER TABLE public.session_metrics DROP CONSTRAINT IF EXISTS session_metrics_metric_type_check;

-- Add new check constraint with additional metric types
ALTER TABLE public.session_metrics 
ADD CONSTRAINT session_metrics_metric_type_check 
CHECK (
  metric_type IN (
    'timeoff', 
    'overtime', 
    'document', 
    'reimbursement',
    'chat',
    'timeoff_approval',
    'timeoff_refusal',
    'overtime_approval',
    'overtime_refusal',
    'log_hours',
    'timeoff_edit',
    'timeoff_cancellation',
    'overtime_edit',
    'overtime_cancellation'
  )
);

