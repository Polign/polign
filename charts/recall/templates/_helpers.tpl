{{- define "recall.labels" -}}
app.kubernetes.io/name: recall
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end }}
{{- define "recall.selector" -}}
app.kubernetes.io/name: recall
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
{{- define "recall.serviceAccount" -}}
{{- if .Values.serviceAccount.create -}}
{{- default .Release.Name .Values.serviceAccount.name -}}
{{- else -}}
{{- required "serviceAccount.name is required when create=false" .Values.serviceAccount.name -}}
{{- end -}}
{{- end }}
{{- define "recall.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}@{{ .Values.image.digest }}
{{- else -}}
{{ .Values.image.repository }}:{{ default .Chart.AppVersion .Values.image.tag }}
{{- end -}}
{{- end }}
{{/* recall.keyEnv is the environment variable the backend driver reads its key from. */}}
{{- define "recall.keyEnv" -}}
{{- if eq .Values.backend.kind "qdrant" -}}QDRANT_API_KEY{{- else -}}POLIGN_API_KEY{{- end -}}
{{- end }}
