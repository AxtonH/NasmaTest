-- Migration: Add approval/refusal metric types and log_hours to session_metrics
-- This allows tracking manager approval actions for timeoff and overtime requests
-- and tracking when users log their hours

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
    'log_hours'
  )
);

