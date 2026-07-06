{{- define "rs.labels" -}}
app.kubernetes.io/part-of: researchscout
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "rs.dbUrl" -}}
postgresql+psycopg://{{ .Values.postgres.user }}:{{ .Values.postgres.password }}@postgres:5432/{{ .Values.postgres.database }}
{{- end }}

{{- define "rs.issuer" -}}
{{ .Values.authUrl }}/realms/researchscout
{{- end }}
