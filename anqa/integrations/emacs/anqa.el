;;; anqa.el --- Live Anqa sessions in Org buffers -*- lexical-binding: t; -*-

;; Package-Requires: ((emacs "29.1"))
;; Keywords: tools, org
;; URL: https://github.com/indynull/anqa

;;; Commentary:

;; View a running Anqa session as an Org buffer and edit operator notes
;; without making the generated trace projection writable.
;;
;; Requires a live control process (``anqad -d`` or TUI auto-start)
;; on the same Unix socket as other clients (HUD, Vim).

;;; Code:

(require 'cl-lib)
(require 'jsonrpc)
(require 'org)
(require 'subr-x)
(require 'term)

(defgroup anqa nil
  "Live Anqa session buffers."
  :group 'tools)

(defcustom anqa-executable "anqa"
  "Executable used by `anqa-start'."
  :type 'string)

(defcustom anqa-control-socket nil
  "Control socket path, or nil to use the per-user runtime default."
  :type '(choice (const :tag "Runtime default" nil) file))

(defcustom anqa-request-timeout 10
  "Seconds to wait for a control request."
  :type 'number)

(defcustom anqa-auto-refresh t
  "When non-nil, reload the session projection on remote notes/trace changes.
If the buffer has unsaved edits, auto-refresh is skipped and a message is shown
instead (same as when this option is nil)."
  :type 'boolean)

(defvar anqa--connection nil)
(defvar anqa--terminal-buffer nil)
(defvar-local anqa-session-id nil)
(defvar-local anqa-session-reference nil)
(defvar-local anqa-notes-revision nil)
(defvar-local anqa-notes-stale nil)
(defvar-local anqa-session-stale nil)
(defvar-local anqa--rendered-note-ids nil
  "Note ids present in the document rendered into this buffer.
Only these ids may be pushed back to the server; anything else was typed
into the buffer and does not name a note.")

(defun anqa--socket-path ()
  "Return the expanded control socket path."
  (expand-file-name
   (or anqa-control-socket
       (let ((runtime (getenv "XDG_RUNTIME_DIR")))
         (if (and runtime (not (string-empty-p runtime)))
             (expand-file-name "anqa/control.sock" runtime)
           (expand-file-name "~/.anqa/run/control.sock"))))))

(defun anqa--method-name (method)
  "Return METHOD as a protocol string."
  (if (symbolp method) (symbol-name method) method))

(defun anqa--maybe-auto-refresh (reason)
  "Reload the projection after a remote change, or message how to reload.
REASON is a short human label (e.g. \"notes changed\")."
  (cond
   ((not anqa-auto-refresh)
    (message "Anqa: %s — C-c C-r (or gr) to reload" reason))
   ((buffer-modified-p)
    (message "Anqa: %s — unsaved note edits; save or C-c C-r to reload" reason))
   (t
    (condition-case err
        (progn
          (anqa--do-refresh)
          (message "Anqa: reloaded (%s)" reason))
      (error
       (message "Anqa: auto-refresh failed: %s" (error-message-string err)))))))

(defun anqa--notification (_connection method params)
  "Handle a server notification named METHOD with PARAMS."
  (let ((session (plist-get params :sessionId)))
    (dolist (buffer (buffer-list))
      (with-current-buffer buffer
        (when (and (derived-mode-p 'anqa-session-mode)
                   (or (null session) (equal anqa-session-id session)))
          (pcase (anqa--method-name method)
            ("notes/changed"
             (let ((rev (plist-get params :revision)))
               ;; Matching revision is the echo of our own upsert/delete, not external drift.
               (if (and rev (equal rev anqa-notes-revision))
                   (setq anqa-notes-stale nil)
                 (let ((was anqa-notes-stale))
                   (setq anqa-notes-stale t)
                   (unless was
                     (anqa--maybe-auto-refresh "notes changed"))))))
            ("session/changed"
             (let ((was anqa-session-stale))
               (setq anqa-session-stale t)
               (unless was
                 (anqa--maybe-auto-refresh "session trace changed"))))
            ("session/selected"
             (let ((prompt-index (plist-get params :promptIndex)))
               (when prompt-index
                 (goto-char (point-min))
                 ;; Prefer the outline headline (readable); property may be folded away.
                 (or (re-search-forward
                      (format "^\\* Prompt %s$" prompt-index)
                      nil t)
                     (re-search-forward
                      (format "^:ANQA_PROMPT_INDEX: %s$" prompt-index)
                      nil t))
                 (beginning-of-line)))))
          (force-mode-line-update))))))

(defun anqa--make-network-process (_connection)
  "Create the local process used by a JSON-RPC CONNECTION."
  (make-network-process
   :name "anqa-control"
   :family 'local
   :service (anqa--socket-path)
   :coding 'utf-8-unix
   :noquery t))

(defun anqa-connected-p ()
  "Return non-nil when the editor control connection is live."
  (and anqa--connection
       (jsonrpc-running-p anqa--connection)))

(defun anqa--drop-connection ()
  "Shut the control connection down and forget it."
  (when anqa--connection
    (ignore-errors (jsonrpc-shutdown anqa--connection))
    (setq anqa--connection nil)))

(defun anqa--request (connection method params)
  "Send METHOD with PARAMS over CONNECTION and return the result.
A connection whose peer died is dropped so the next command reconnects."
  (condition-case err
      (jsonrpc-request connection method params
                       :timeout anqa-request-timeout)
    (error
     (unless (anqa-connected-p)
       (anqa--drop-connection))
     (signal (car err) (cdr err)))))

(defun anqa-disconnect ()
  "Close the editor control connection."
  (interactive)
  (anqa--drop-connection))

(defun anqa-connect ()
  "Connect to the running Anqa control socket."
  (interactive)
  (unless (anqa-connected-p)
    (unless (file-exists-p (anqa--socket-path))
      (user-error "Anqa control socket does not exist: %s" (anqa--socket-path)))
    (condition-case err
        (progn
          (setq anqa--connection
                (make-instance
                 'jsonrpc-process-connection
                 :name "anqa"
                 :process #'anqa--make-network-process
                 :notification-dispatcher #'anqa--notification
                 :events-buffer-config '(:size 200)))
          (anqa--request
           anqa--connection
           "initialize"
           `(:protocolVersion "1.0.0"
             :clientInfo (:name "Emacs" :version ,emacs-version))))
      (error
       (anqa--drop-connection)
       (signal (car err) (cdr err)))))
  anqa--connection)

(defun anqa--wait-for-socket (process)
  "Wait for the control socket owned by PROCESS."
  (let ((deadline (+ (float-time) anqa-request-timeout)))
    (while (and (process-live-p process)
                (not (file-exists-p (anqa--socket-path)))
                (< (float-time) deadline))
      (accept-process-output process 0.05))
    (unless (file-exists-p (anqa--socket-path))
      (user-error "Anqa did not create its control socket"))))

(defun anqa-start (&optional session prompt-index)
  "Start the TUI for SESSION and optionally select PROMPT-INDEX."
  (interactive)
  (let* ((socket (anqa--socket-path))
         (args (append
                (when session (list "--path" (expand-file-name session)))
                (list "--control-socket" socket)
                (when prompt-index
                  (list "--prompt-index" (number-to-string prompt-index)))))
         (buffer (apply #'make-term "anqa-live" anqa-executable nil args))
         (process (get-buffer-process buffer)))
    (setq anqa--terminal-buffer buffer)
    (set-process-query-on-exit-flag process nil)
    (with-current-buffer buffer
      (term-mode)
      (term-char-mode))
    (display-buffer buffer '(display-buffer-at-bottom (window-height . 14)))
    (anqa--wait-for-socket process)
    buffer))

(defun anqa--connection-refused-p (err)
  "Return non-nil when ERR is a transient control-socket connect failure.
Matches connection refused, missing path, and macOS EAGAIN
\(\"Resource temporarily unavailable\" / os error 35\)."
  (or (eq (car err) 'file-error)
      (let ((message (downcase (error-message-string err))))
        (or (string-match-p "connection refused" message)
            (string-match-p "no such file or directory" message)
            (string-match-p "resource temporarily unavailable" message)
            (string-match-p "os error 35" message)))))

(defun anqa--connect-retrying (deadline)
  "Connect, retrying refused connections until DEADLINE.
A TUI taking a stale socket over needs a moment before it accepts clients."
  (let (connected)
    (while (not connected)
      (condition-case err
          (progn (anqa-connect) (setq connected t))
        (error
         (anqa--drop-connection)
         (when (or (not (anqa--connection-refused-p err))
                   (>= (float-time) deadline))
           (signal (car err) (cdr err)))
         (sleep-for 0.1)))))
  anqa--connection)

(defun anqa--connection-for-session (session)
  "Return a live connection, starting Anqa for SESSION when needed."
  (unless (anqa-connected-p)
    (let ((directory (and session
                          (file-directory-p session)
                          session)))
      (unless (file-exists-p (anqa--socket-path))
        (anqa-start directory nil))
      (condition-case err
          (anqa-connect)
        (error
         (anqa--drop-connection)
         ;; A socket file outliving its TUI refuses connections; starting the
         ;; TUI again takes the stale socket over.
         (unless (and directory (anqa--connection-refused-p err))
           (signal (car err) (cdr err)))
         (anqa-start directory nil)
         (anqa--connect-retrying (+ (float-time) anqa-request-timeout))))))
  anqa--connection)

(defun anqa--normalize-session-reference (session)
  "Expand SESSION when it names a directory and preserve catalog ids."
  (if (file-directory-p session)
      (file-truename session)
    session))

(defun anqa--field-body-region ()
  "Return the body region of the Org field at point.
The region ends with the newline of the last content line, so the blank
separator line before the next heading stays outside the writable span and
Org structure cannot be typed at column 0 inside a field."
  (save-excursion
    (org-back-to-heading t)
    (let ((limit (org-entry-end-position)))
      (org-end-of-meta-data t)
      (let ((begin (point))
            (end limit))
        (save-excursion
          (goto-char limit)
          (skip-chars-backward " \t\n" begin)
          ;; A body of blank lines only has no content line to stop after.
          (when (> (point) begin)
            (forward-line 1)
            (setq end (min limit (point)))))
        (cons begin (max begin end))))))

(defun anqa--editable-field-regions ()
  "Return body regions for headings with a ANQA_FIELD_ID property."
  (let (regions)
    (org-map-entries
     (lambda ()
       (when (org-entry-get nil "ANQA_FIELD_ID" nil)
         (push (anqa--field-body-region) regions)))
     nil nil)
    regions))

(defun anqa--document-note-ids ()
  "Return the ANQA_NOTE_ID values present in the current buffer."
  (let (ids)
    (org-map-entries
     (lambda ()
       (let ((note-id (org-entry-get nil "ANQA_NOTE_ID" nil)))
         (when note-id (push note-id ids))))
     nil nil)
    (nreverse ids)))

(defun anqa--apply-document (text session-id revision reference)
  "Replace the current buffer with TEXT and configure session metadata."
  (let ((inhibit-read-only t))
    (erase-buffer)
    (insert text)
    (goto-char (point-min))
    (setq anqa--rendered-note-ids (anqa--document-note-ids))
    (goto-char (point-min))
    (let ((regions (anqa--editable-field-regions)))
      (add-text-properties
       (point-min) (point-max)
       '(read-only t front-sticky (read-only) rear-nonsticky (read-only)))
      (dolist (region regions)
        (remove-text-properties (car region) (cdr region) '(read-only nil)))))
  (setq anqa-session-id session-id
        anqa-session-reference reference
        anqa-notes-revision revision
        anqa-notes-stale nil
        anqa-session-stale nil)
  (set-buffer-modified-p nil)
  ;; Native src fontify (Markdown transcript); keep body un-indented for tables.
  (setq-local org-src-fontify-natively t)
  (setq-local org-src-preserve-indentation t)
  (setq-local org-edit-src-content-indentation 0)
  ;; Pipe tables stay aligned only when lines are not soft-wrapped.
  (setq-local truncate-lines t)
  (setq-local org-hide-drawer-startup t)
  (when (fboundp 'org-restart-font-lock)
    (org-restart-font-lock))
  ;; Fold property drawers (machine tags) for a calmer outline.
  (save-excursion
    (goto-char (point-min))
    (when (fboundp 'org-cycle-hide-drawers)
      (org-cycle-hide-drawers 'all)))
  (goto-char (point-min)))

(defun anqa--ancestor-property (property)
  "Return PROPERTY from the nearest heading ancestor."
  (save-excursion
    (org-back-to-heading t)
    (catch 'found
      (while t
        (let ((value (org-entry-get nil property nil)))
          (when value (throw 'found value)))
        (unless (org-up-heading-safe) (throw 'found nil))))))

(defun anqa--prompt-index-at-point ()
  "Return the source prompt index at point."
  (let ((raw (anqa--ancestor-property "ANQA_PROMPT_INDEX")))
    (and raw (string-to-number raw))))

(defun anqa--strip-org-fixed-line (line)
  "Undo Org fixed-width prefix (`: ' / `:') from a rendered field LINE."
  (cond
   ((string-prefix-p ": " line) (substring line 2))
   ((string-equal ":" line) "")
   (t line)))

(defun anqa--field-value-at-point ()
  "Return the current field body without generated properties.
Strips Org fixed-width lines used when rendering field values so outline
stars inside a value cannot form headlines, then round-trip cleanly.
Leading and trailing blank lines are part of the value."
  (pcase-let ((`(,begin . ,end) (anqa--field-body-region)))
    (let* ((raw (buffer-substring-no-properties begin end))
           ;; Region end is the newline after the last content line; that
           ;; delimiter is not part of the value.
           (raw (if (string-suffix-p "\n" raw)
                    (substring raw 0 -1)
                  raw))
           ;; Keep empty lines (omit-nulls nil).
           (lines (split-string raw "\n" nil)))
      (mapconcat #'anqa--strip-org-fixed-line lines "\n"))))

(defun anqa--note-at-point ()
  "Return the operator note containing point as a JSON-ready plist."
  (save-excursion
    (let ((note-id (anqa--ancestor-property "ANQA_NOTE_ID")))
      (unless note-id (user-error "Point is not inside an operator note"))
      (while (not (org-entry-get nil "ANQA_NOTE_ID" nil))
        (org-up-heading-safe))
      (let* ((note-start (point))
             (note-end (save-excursion (org-end-of-subtree t t) (point)))
             (turn-index (string-to-number
                          (or (anqa--ancestor-property "ANQA_TURN_INDEX") "0")))
             (event-text (or (org-entry-get nil "ANQA_EVENT_INDICES" nil) ""))
             (created-at (or (org-entry-get nil "ANQA_CREATED_AT" nil) ""))
             (source (or (org-entry-get nil "ANQA_SOURCE" nil) "emacs"))
             (updated-at (format-time-string "%FT%T%:z" nil t))
             (fields (make-hash-table :test #'equal)))
        (save-restriction
          (narrow-to-region note-start note-end)
          (org-map-entries
           (lambda ()
             (let ((field-id (org-entry-get nil "ANQA_FIELD_ID" nil)))
               (when field-id
                 (puthash field-id (anqa--field-value-at-point) fields))))
           nil 'tree))
        `(:id ,note-id
          :turnIndex ,turn-index
          :source ,(if (and source (not (string-empty-p source))) source "emacs")
          :fields ,fields
          :eventIndices ,(vconcat
                          (mapcar #'string-to-number
                                  (split-string event-text "," t "[[:space:]]+")))
          :createdAt ,created-at
          :updatedAt ,updated-at)))))

(defun anqa--render-session (session)
  "Request the Org projection for SESSION."
  (anqa--request
   (anqa--connection-for-session session)
   "session/render"
   `(:session ,session)))

(defun anqa--do-refresh ()
  "Reload the projection without prompting (caller checks dirty state).
Changes announced while the render request is in flight postdate the
response, so their stale flags survive the reload."
  (let* ((reference (or anqa-session-reference anqa-session-id))
         (point-before (point))
         (notes-stale anqa-notes-stale)
         (session-stale anqa-session-stale)
         result)
    (setq anqa-notes-stale nil
          anqa-session-stale nil)
    (condition-case err
        (setq result (anqa--render-session reference))
      (error
       (setq anqa-notes-stale (or anqa-notes-stale notes-stale)
             anqa-session-stale (or anqa-session-stale session-stale))
       (signal (car err) (cdr err))))
    (setq notes-stale anqa-notes-stale
          session-stale anqa-session-stale)
    (anqa--apply-document
     (plist-get result :text)
     (plist-get result :sessionId)
     (plist-get result :notesRevision)
     reference)
    (setq anqa-notes-stale notes-stale
          anqa-session-stale session-stale)
    (force-mode-line-update)
    (goto-char (min point-before (point-max)))))

(defun anqa-refresh ()
  "Reload the current session projection from Anqa."
  (interactive)
  (when (and (buffer-modified-p)
             (not (yes-or-no-p "Discard unsaved note edits? ")))
    (user-error "Refresh cancelled"))
  (anqa--do-refresh))

(defun anqa--session-list (&optional query limit offset)
  "Return one `session/list' page for QUERY, optional LIMIT, and OFFSET."
  (let ((params (list :query (or query ""))))
    (when limit
      (setq params (plist-put params :limit limit)))
    (when offset
      (setq params (plist-put params :offset offset)))
    (anqa--request (anqa-connect) "session/list" params)))

(defconst anqa--session-list-page 200
  "Page size when draining `session/list'.")

(defun anqa--session-list-all (&optional query)
  "Drain `session/list' pages for QUERY until `matched'.
Pages keep the catalog's newest-activity-first order."
  (let ((sessions nil)
        (offset 0)
        (matched 0)
        (total 0)
        (first-id "")
        (done nil))
    (while (not done)
      (let* ((result (anqa--session-list query anqa--session-list-page offset))
             (batch (append (plist-get result :sessions) nil))
             (batch-first (or (plist-get (car batch) :sessionId) "")))
        (setq matched (or (plist-get result :matched) matched)
              total (or (plist-get result :total) total))
        (cond
         ((null batch)
          (setq done t))
         ((and (> offset 0)
               (not (string-empty-p first-id))
               (string-equal batch-first first-id))
          (setq done t))
         (t
          (when (zerop offset)
            (setq first-id batch-first))
          (setq sessions (nconc sessions (copy-sequence batch))
                offset (+ offset (length batch)))
          (when (or (< (length batch) anqa--session-list-page)
                    (and (> matched 0) (>= offset matched)))
            (setq done t))))))
    (list :sessions sessions :matched matched :total total)))

(defun anqa--session-entry-path (entry)
  "Return a stable open reference for session ENTRY."
  (or (plist-get entry :path)
      (plist-get entry :sessionId)))

(defun anqa--session-entry-harness (entry)
  "Return the product label for ENTRY (`harnessLabel', else `harness')."
  (let ((label (or (plist-get entry :harnessLabel) ""))
        (id (or (plist-get entry :harness) "")))
    (cond
     ((and label (not (string-empty-p label))) label)
     ((and id (not (string-empty-p id))) id)
     (t ""))))

(defun anqa--session-entry-annotation (entry)
  "Return a one-line label for catalog ENTRY."
  (let* ((title (or (plist-get entry :title) (plist-get entry :label) ""))
         (session-id (or (plist-get entry :sessionId) ""))
         (status (or (plist-get entry :status) ""))
         (model (or (plist-get entry :model) ""))
         (harness (anqa--session-entry-harness entry))
         (head (if (and title (not (string-empty-p title)))
                   title
                 session-id)))
    (string-trim
     (mapconcat #'identity
                (delq nil
                      (list (and status (not (string-empty-p status)) status)
                            (and harness (not (string-empty-p harness)) harness)
                            head
                            (and model (not (string-empty-p model)) model)
                            ;; Avoid "id · id" when the title fell back to session-id.
                            (and session-id
                                 (not (string-empty-p session-id))
                                 (not (string-equal session-id head))
                                 session-id)))
                "  ·  "))))

(defun anqa-list-sessions (&optional query)
  "List sessions from the running TUI catalog, optionally filtered by QUERY.
With a prefix argument, prompt for QUERY. Results open a read-only buffer."
  (interactive
   (list (if current-prefix-arg
             (read-string "Filter sessions: ")
           "")))
  (anqa-connect)
  (let* ((result (anqa--session-list-all query))
         (sessions (append (plist-get result :sessions) nil))
         (total (or (plist-get result :total) 0))
         (matched (or (plist-get result :matched) (length sessions)))
         (buffer (get-buffer-create "*anqa-sessions*")))
    (with-current-buffer buffer
      (let ((inhibit-read-only t))
        (erase-buffer)
        (insert
         (format "Anqa sessions  matched %s / total %s"
                 matched total)
         (if (and query (not (string-empty-p query)))
             (format "  filter: %s\n\n" query)
           "\n\n"))
        (if (null sessions)
            (insert "(no sessions)\n")
          (dolist (entry sessions)
            (let ((path (anqa--session-entry-path entry))
                  (line (anqa--session-entry-annotation entry)))
              (insert-text-button
               line
               'action (lambda (_button)
                         (anqa-open-session path))
               'follow-link t
               'anqa-session path
               'help-echo path)
              (insert "\n")))))
      (goto-char (point-min))
      (setq buffer-read-only t)
      (special-mode))
    (pop-to-buffer buffer)
    buffer))

(defun anqa-find-session (&optional query)
  "Pick a catalog session with completion and open it as an Org buffer.
QUERY pre-filters the catalog on the server when non-empty. With a prefix
argument, prompt for QUERY first."
  (interactive
   (list (if current-prefix-arg
             (read-string "Filter sessions: ")
           "")))
  (anqa-connect)
  (let* ((result (anqa--session-list-all query))
         (sessions (append (plist-get result :sessions) nil))
         (table (make-hash-table :test #'equal))
         candidates)
    (when (null sessions)
      (user-error "No sessions matched%s"
                  (if (and query (not (string-empty-p query)))
                      (format " %S" query)
                    "")))
    (dolist (entry sessions)
      (let* ((annotation (anqa--session-entry-annotation entry))
             (path (anqa--session-entry-path entry))
             (key annotation)
             (n 2))
        (while (gethash key table)
          (setq key (format "%s (%s)" annotation n)
                n (1+ n)))
        (puthash key path table)
        (push key candidates)))
    (let* ((choice (completing-read "Anqa session: " (nreverse candidates) nil t))
           (path (gethash choice table)))
      (unless path
        (user-error "No session selected"))
      (anqa-open-session path))))

(defun anqa-open-session (session &optional prompt-index)
  "Open SESSION as an Org buffer and select PROMPT-INDEX in the TUI."
  (interactive (list (read-string "Session path or id: ") nil))
  (let* ((reference (anqa--normalize-session-reference session))
         (connection (anqa--connection-for-session reference))
         (result (anqa--request
                  connection "session/render" `(:session ,reference)))
         (session-id (plist-get result :sessionId))
         (name (format "*anqa:%s*" session-id))
         (existing (get-buffer name))
         ;; Re-rendering an open buffer throws its edits away, so ask first.
         (render (or (null existing)
                     (not (buffer-modified-p existing))
                     (yes-or-no-p
                      (format "%s has unsaved note edits; discard them? " name))))
         (buffer (or existing (get-buffer-create name))))
    (when render
      (with-current-buffer buffer
        (anqa-session-mode)
        (anqa--apply-document
         (plist-get result :text)
         session-id
         (plist-get result :notesRevision)
         reference)))
    (anqa--request
     connection "session/open"
     `(:session ,reference :promptIndex ,prompt-index))
    (pop-to-buffer buffer)
    buffer))
(defun anqa-open-prompt-at-point ()
  "Select the prompt at point in the running TUI."
  (interactive)
  (let ((prompt-index (anqa--prompt-index-at-point)))
    (unless prompt-index (user-error "Point is not inside a prompt"))
    (anqa--request
     (anqa-connect) "session/open"
     `(:session ,anqa-session-reference :promptIndex ,prompt-index))))

(defun anqa-open-at-point ()
  "Open the subagent child at point, or select the prompt in the TUI."
  (interactive)
  (let ((child (anqa--ancestor-property "ANQA_CHILD_SESSION")))
    (if (and child (not (string-empty-p child)))
        (anqa-open-session child)
      (anqa-open-prompt-at-point))))

(defun anqa--rendered-note-id-p (note-id)
  "Return non-nil when NOTE-ID belongs to the rendered document."
  (and note-id (member note-id anqa--rendered-note-ids) t))

(defun anqa-save-note ()
  "Save the operator note at point with revision checking.
The buffer stays modified: other notes may still hold unsaved edits."
  (interactive)
  (let ((note (anqa--note-at-point)))
    (unless (anqa--rendered-note-id-p (plist-get note :id))
      (user-error "Note %s is not part of this session projection"
                  (plist-get note :id)))
    (let ((result
           (anqa--request
            (anqa-connect) "notes/upsert"
            `(:session ,anqa-session-reference
              :expectedRevision ,anqa-notes-revision
              :note ,note))))
      (setq anqa-notes-revision (plist-get result :revision)
            anqa-notes-stale nil)
      (force-mode-line-update)
      result)))

(defun anqa--new-note-id ()
  "Return a locally unique note id."
  (concat
   "n-"
   (substring
    (secure-hash 'sha256 (format "%s:%s:%s" (float-time) (random) (emacs-pid)))
    0 12)))

(defun anqa--require-saved (action)
  "Refuse ACTION while the buffer holds unsaved note edits.
ACTION reloads the projection, which would drop those edits."
  (when (buffer-modified-p)
    (user-error "Unsaved note edits; C-x C-s to save or C-c C-r to reload before %s"
                action)))

(defun anqa-new-note ()
  "Create an operator note under the prompt at point."
  (interactive)
  (anqa--require-saved "creating a note")
  (let* ((turn-text (anqa--ancestor-property "ANQA_TURN_INDEX"))
         (prompt-index (anqa--prompt-index-at-point)))
    (unless (and turn-text prompt-index)
      (user-error "Point is not inside a prompt"))
    (let* ((connection (anqa-connect))
           (listed (anqa--request
                    connection "notes/list"
                    `(:session ,anqa-session-reference)))
           (schema (plist-get listed :schema))
           (field-specs (plist-get schema :fields))
           (fields (make-hash-table :test #'equal))
           (timestamp (format-time-string "%FT%T%:z" nil t))
           (note-id (anqa--new-note-id))
           (note
            `(:id ,note-id
              :turnIndex ,(string-to-number turn-text)
              :source "emacs"
              :fields ,fields
              :eventIndices []
              :createdAt ,timestamp
              :updatedAt ,timestamp)))
      (mapc
       (lambda (spec)
         (let ((field-id (plist-get spec :id)))
           (when field-id (puthash field-id "" fields))))
       field-specs)
      (let ((result
             (anqa--request
              connection "notes/upsert"
              `(:session ,anqa-session-reference
                :expectedRevision ,anqa-notes-revision
                :note ,note))))
        (setq anqa-notes-revision (plist-get result :revision))
        ;; The note exists on the server; reload without a prompt that could
        ;; abort and leave the buffer disagreeing with it.
        (anqa--do-refresh)
        (goto-char (point-min))
        (when (re-search-forward
               (format "^:ANQA_NOTE_ID: %s$" (regexp-quote note-id))
               nil t)
          (org-back-to-heading t)
          ;; Prefer first field body (editable) under the note.
          (when (re-search-forward "^:ANQA_FIELD_ID:" nil t)
            (org-end-of-meta-data t)))))))

(defun anqa-delete-note (&optional no-confirm)
  "Delete the note at point, asking first unless NO-CONFIRM is non-nil."
  (interactive)
  (anqa--require-saved "deleting a note")
  (let ((note-id (anqa--ancestor-property "ANQA_NOTE_ID")))
    (unless note-id (user-error "Point is not inside an operator note"))
    (when (or no-confirm (yes-or-no-p (format "Delete note %s? " note-id)))
      (let ((result
             (anqa--request
              (anqa-connect) "notes/delete"
              `(:session ,anqa-session-reference
                :expectedRevision ,anqa-notes-revision
                :noteId ,note-id))))
        (setq anqa-notes-revision (plist-get result :revision))
        ;; The delete happened; reload without a prompt that could abort.
        (anqa--do-refresh)))))

(defun anqa-save-buffer ()
  "Save every operator note in the current session buffer.
Headings carrying a note id the projection never rendered name no note on
the server, so they are reported and left alone."
  (interactive)
  (save-excursion
    (let (entries skipped)
      (goto-char (point-min))
      (org-map-entries
       (lambda ()
         (let ((note-id (org-entry-get nil "ANQA_NOTE_ID" nil)))
           (when note-id
             (push (cons note-id (copy-marker (point))) entries))))
       nil nil)
      (dolist (entry (nreverse entries))
        (if (anqa--rendered-note-id-p (car entry))
            (progn
              (goto-char (cdr entry))
              (anqa-save-note))
          (push (car entry) skipped)))
      (when skipped
        (message "Anqa: skipped %d unknown note id(s): %s"
                 (length skipped)
                 (mapconcat #'identity (nreverse skipped) ", ")))))
  (set-buffer-modified-p nil)
  t)

(defun anqa--other-session-buffer-p ()
  "Return non-nil when another live session buffer exists."
  (cl-some (lambda (buffer)
             (and (not (eq buffer (current-buffer)))
                  (buffer-live-p buffer)
                  (with-current-buffer buffer
                    (derived-mode-p 'anqa-session-mode))))
           (buffer-list)))

(defun anqa--kill-buffer-hook ()
  "Drop the control connection with the last session buffer.
The TUI terminal buffer stays: it belongs to the user, not to this client."
  (unless (anqa--other-session-buffer-p)
    (anqa--drop-connection)))

(defvar-keymap anqa-session-mode-map
  :parent org-mode-map
  ;; Do not bind bare ``g`` — in Evil/Doom it is a motion prefix (``gg``, …).
  "C-c C-r" #'anqa-refresh
  "C-c C-n" #'anqa-new-note
  "C-c C-k" #'anqa-delete-note
  "C-c C-o" #'anqa-open-at-point
  "C-c C-c" #'anqa-save-note
  "C-x C-s" #'anqa-save-buffer)

(define-derived-mode anqa-session-mode org-mode "Anqa"
  "Major mode for live Anqa Org session buffers.

Transcript is read-only Markdown in source blocks; only note field bodies edit.
Keys: C-c C-c save note, C-x C-s save all, C-c C-n new note, C-c C-k delete,
C-c C-o open child or select prompt in TUI, C-c C-r refresh. In Doom/Evil, gr also refreshes."
  (setq-local write-contents-functions '(anqa-save-buffer))
  (add-hook 'kill-buffer-hook #'anqa--kill-buffer-hook nil t)
  (setq-local org-src-fontify-natively t)
  (setq-local org-src-preserve-indentation t)
  (setq-local org-edit-src-content-indentation 0)
  (setq-local truncate-lines t)
  (setq-local org-hide-drawer-startup t)
  (setq-local mode-line-process
              '(:eval
                (concat
                 (when anqa-session-stale " Trace changed")
                 (when anqa-notes-stale " Notes changed")))))

;; Evil/Doom: gr refresh without stealing g (motion prefix).
;; `evil-define-key*' is a function, so this body survives byte compilation
;; without Evil loaded at compile time.
(declare-function evil-define-key* "evil-core" (state keymap key def &rest bindings))

(with-eval-after-load 'evil
  (when (fboundp 'evil-define-key*)
    (evil-define-key* 'normal anqa-session-mode-map (kbd "gr") #'anqa-refresh)))

(provide 'anqa)
;;; anqa.el ends here
