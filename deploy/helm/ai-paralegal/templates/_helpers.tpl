{{- define "ai-paralegal.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ai-paralegal.fullname" -}}
{{- printf "%s" (include "ai-paralegal.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ai-paralegal.labels" -}}
app.kubernetes.io/name: {{ include "ai-paralegal.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "ai-paralegal.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{ include "ai-paralegal.fullname" . }}-secrets
{{- end -}}
{{- end -}}
