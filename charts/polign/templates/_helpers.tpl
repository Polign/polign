{{- define "polign.labels" -}}
app.kubernetes.io/name: polign
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end }}
{{- define "polign.selector" -}}
app.kubernetes.io/name: polign
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
{{- define "polign.serviceAccount" -}}
{{- if .Values.serviceAccount.create -}}
{{- default .Release.Name .Values.serviceAccount.name -}}
{{- else -}}
{{- required "serviceAccount.name is required when create=false" .Values.serviceAccount.name -}}
{{- end -}}
{{- end }}
{{- define "polign.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}@{{ .Values.image.digest }}
{{- else -}}
{{ .Values.image.repository }}:{{ default .Chart.AppVersion .Values.image.tag }}
{{- end -}}
{{- end }}
{{/*
polign.store resolves the store URI, defaulting to the local filesystem when the
chart is asked to provision its own volume. That default is what lets someone
try Polign without first creating a bucket and a cloud identity, which is the
only part of an install that cannot be done from inside Kubernetes.
*/}}
{{- define "polign.store" -}}
{{- if and .Values.store.claim.create (not .Values.store.uri) -}}
fs:/var/lib/polign
{{- else -}}
{{ required "Set store.uri to your bucket URI, or store.claim.create=true to evaluate on a local volume" .Values.store.uri }}
{{- end -}}
{{- end }}
{{- define "polign.claimName" -}}
{{- if .Values.store.existingClaim -}}
{{ .Values.store.existingClaim }}
{{- else -}}
{{ .Release.Name }}-data
{{- end -}}
{{- end }}
