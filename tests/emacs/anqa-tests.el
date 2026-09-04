;;; anqa-tests.el --- Tests for Anqa Org integration -*- lexical-binding: t; -*-

;;; Commentary:

;; Run from the repository root:
;;
;;   emacs --batch -L anqa/integrations/emacs -l ert \
;;         -l tests/emacs/anqa-tests.el -f ert-run-tests-batch-and-exit
;;
;; Only ert, org and jsonrpc are needed; control requests are stubbed.

;;; Code:

(require 'cl-lib)
(require 'ert)
(require 'org)
(require 'anqa)

(defconst anqa-test--document
  "#+TITLE: Socket review
#+PROPERTY: ANQA_SESSION_ID session-emacs
#+PROPERTY: ANQA_NOTES_REVISION rev-1

* Prompt 6
:PROPERTIES:
:ANQA_PROMPT_INDEX: 6
:ANQA_TURN_INDEX: 0
:END:

** User

: inspect this

** Operator notes

*** Original summary
:PROPERTIES:
:ANQA_NOTE_ID: n-emacs
:ANQA_EVENT_INDICES: 1,2
:ANQA_CREATED_AT: 2026-08-01T12:00:00+00:00
:ANQA_UPDATED_AT: 2026-08-01T12:00:00+00:00
:END:

**** Summary
:PROPERTIES:
:ANQA_FIELD_ID: summary
:END:
Original summary

**** Detail
:PROPERTIES:
:ANQA_FIELD_ID: detail
:END:
Original detail
")

(defconst anqa-test--two-note-document
  "#+TITLE: Socket review
#+PROPERTY: ANQA_SESSION_ID session-emacs
#+PROPERTY: ANQA_NOTES_REVISION rev-1

* Prompt 6
:PROPERTIES:
:ANQA_PROMPT_INDEX: 6
:ANQA_TURN_INDEX: 0
:END:

** User

: inspect this

** Operator notes

*** First
:PROPERTIES:
:ANQA_NOTE_ID: n-first
:ANQA_EVENT_INDICES: 1
:ANQA_CREATED_AT: 2026-08-01T12:00:00+00:00
:ANQA_UPDATED_AT: 2026-08-01T12:00:00+00:00
:END:

**** Summary
:PROPERTIES:
:ANQA_FIELD_ID: summary
:END:
: First summary

**** Detail
:PROPERTIES:
:ANQA_FIELD_ID: detail
:END:
: First detail

*** Second
:PROPERTIES:
:ANQA_NOTE_ID: n-second
:ANQA_EVENT_INDICES: 2
:ANQA_CREATED_AT: 2026-08-01T12:05:00+00:00
:ANQA_UPDATED_AT: 2026-08-01T12:05:00+00:00
:END:

**** Summary
:PROPERTIES:
:ANQA_FIELD_ID: summary
:END:
: Second summary

**** Detail
:PROPERTIES:
:ANQA_FIELD_ID: detail
:END:
: Second detail
")

(defun anqa-test--render (&optional text)
  "Render TEXT (or the two-note document) into the current buffer."
  (anqa-session-mode)
  (anqa--apply-document (or text anqa-test--two-note-document)
                          "session-emacs" "rev-1" "session-emacs"))

(defun anqa-test--append (search text)
  "Type TEXT at the end of the field body containing SEARCH."
  (goto-char (point-min))
  (search-forward search)
  (insert text))

(defun anqa-test--render-result (&optional text)
  "Return a `session/render' result carrying TEXT."
  (list :text (or text anqa-test--two-note-document)
        :sessionId "session-emacs"
        :notesRevision "rev-1"))


;;; Projection and read-only regions

(ert-deftest anqa-document-protects-trace-and-opens-note-fields ()
  (with-temp-buffer
    (anqa-session-mode)
    (anqa--apply-document anqa-test--document "session-emacs" "rev-1" "session-emacs")
    (goto-char (point-min))
    (search-forward "inspect this")
    (should (get-text-property (point) 'read-only))
    (should-error (insert "x") :type 'text-read-only)
    (goto-char (point-min))
    (search-forward "Original detail")
    (should-not (get-text-property (1- (point)) 'read-only))
    (insert " amended")
    (should (string-match-p "Original detail amended" (buffer-string)))))

(ert-deftest anqa-field-body-stops-before-the-blank-separator ()
  "Structure typed at column 0 must not land inside a field body."
  (with-temp-buffer
    (anqa-test--render)
    (goto-char (point-min))
    (search-forward ": First summary")
    (insert " tail")
    (should (string-match-p ": First summary tail" (buffer-string)))
    (forward-line 1)
    (beginning-of-line)
    (should (looking-at-p "^$"))
    (should (get-text-property (point) 'read-only))
    (should-error (insert "*** fabricated") :type 'text-read-only)))

(ert-deftest anqa-document-parses-note-at-point ()
  (with-temp-buffer
    (anqa-session-mode)
    (anqa--apply-document anqa-test--document "session-emacs" "rev-1" "session-emacs")
    (goto-char (point-min))
    (search-forward "Original detail")
    (let* ((note (anqa--note-at-point))
           (fields (plist-get note :fields)))
      (should (equal (plist-get note :id) "n-emacs"))
      (should (= (plist-get note :turnIndex) 0))
      (should (equal (plist-get note :eventIndices) [1 2]))
      (should (equal (gethash "summary" fields) "Original summary"))
      (should (equal (gethash "detail" fields) "Original detail")))))

(ert-deftest anqa-field-value-keeps-leading-and-trailing-blank-lines ()
  "Blank lines inside a fixed-width field body survive save-parse."
  (with-temp-buffer
    (anqa-session-mode)
    (anqa--apply-document
     "#+TITLE: blanks
#+PROPERTY: ANQA_SESSION_ID session-emacs
#+PROPERTY: ANQA_NOTES_REVISION rev-1

* Prompt 1
:PROPERTIES:
:ANQA_PROMPT_INDEX: 1
:ANQA_TURN_INDEX: 0
:END:

** Operator notes

*** Note
:PROPERTIES:
:ANQA_NOTE_ID: n-blank
:ANQA_EVENT_INDICES: 1
:ANQA_CREATED_AT: 2026-08-01T12:00:00+00:00
:ANQA_UPDATED_AT: 2026-08-01T12:00:00+00:00
:END:

**** Summary
:PROPERTIES:
:ANQA_FIELD_ID: summary
:END:
:
: alpha
:

**** Detail
:PROPERTIES:
:ANQA_FIELD_ID: detail
:END:
:
: text
"
     "session-emacs" "rev-1" "session-emacs")
    (goto-char (point-min))
    (search-forward "alpha")
    (let* ((note (anqa--note-at-point))
           (fields (plist-get note :fields)))
      (should (equal (gethash "summary" fields) "\nalpha\n"))
      (should (equal (gethash "detail" fields) "\ntext")))))

(ert-deftest anqa-document-records-rendered-note-ids ()
  (with-temp-buffer
    (anqa-test--render)
    (should (equal anqa--rendered-note-ids '("n-first" "n-second")))
    (should (anqa--rendered-note-id-p "n-first"))
    (should-not (anqa--rendered-note-id-p "n-typed"))))

(ert-deftest anqa-document-finds-source-prompt-index ()
  (with-temp-buffer
    (anqa-session-mode)
    (anqa--apply-document anqa-test--document "session-emacs" "rev-1" "session-emacs")
    (goto-char (point-min))
    (search-forward "inspect this")
    (should (= (anqa--prompt-index-at-point) 6))))

(ert-deftest anqa-session-reference-preserves-catalog-ids ()
  (should (equal (anqa--normalize-session-reference "session-emacs")
                 "session-emacs"))
  (let ((directory (make-temp-file "anqa-session-" t)))
    (unwind-protect
        (should (equal (anqa--normalize-session-reference directory)
                       (file-truename directory)))
      (delete-directory directory))))


;;; Saving

(ert-deftest anqa-save-note-keeps-other-unsaved-edits-visible ()
  "One saved note must not advertise the whole buffer as saved."
  (with-temp-buffer
    (anqa-test--render)
    (anqa-test--append ": First detail" " one")
    (anqa-test--append ": Second detail" " two")
    (should (buffer-modified-p))
    (let (sent)
      (cl-letf (((symbol-function 'anqa-connect) (lambda () 'connection))
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection method params &rest _keys)
                   (should (equal method "notes/upsert"))
                   (push (plist-get params :note) sent)
                   '(:revision "rev-2"))))
        (goto-char (point-min))
        (search-forward ": First detail")
        (anqa-save-note))
      (should (= (length sent) 1))
      (should (equal (plist-get (car sent) :id) "n-first"))
      (should (equal (gethash "detail" (plist-get (car sent) :fields))
                     "First detail one"))
      (should (buffer-modified-p)))))

(ert-deftest anqa-save-buffer-saves-every-note-and-clears-modified ()
  (with-temp-buffer
    (anqa-test--render)
    (anqa-test--append ": First detail" " one")
    (anqa-test--append ": Second detail" " two")
    (goto-char (point-min))
    (search-forward ": Second summary")
    (let ((point-before (point))
          ids)
      (cl-letf (((symbol-function 'anqa-connect) (lambda () 'connection))
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection _method params &rest _keys)
                   (push (plist-get (plist-get params :note) :id) ids)
                   '(:revision "rev-2"))))
        (anqa-save-buffer))
      (should (equal (sort ids #'string<) '("n-first" "n-second")))
      (should-not (buffer-modified-p))
      (should (= (point) point-before)))))

(ert-deftest anqa-save-buffer-skips-notes-outside-the-projection ()
  "A note id typed into the buffer names nothing on the server."
  (with-temp-buffer
    (anqa-test--render)
    (let ((inhibit-read-only t))
      (goto-char (point-max))
      (insert "\n*** Fabricated\n:PROPERTIES:\n:ANQA_NOTE_ID: n-typed\n:END:\n\n"
              "**** Summary\n:PROPERTIES:\n:ANQA_FIELD_ID: summary\n:END:\n: typed\n"))
    (let (ids)
      (cl-letf (((symbol-function 'anqa-connect) (lambda () 'connection))
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection _method params &rest _keys)
                   (push (plist-get (plist-get params :note) :id) ids)
                   '(:revision "rev-2"))))
        (anqa-save-buffer))
      (should (equal (sort ids #'string<) '("n-first" "n-second")))
      (should-not (member "n-typed" ids)))))

(ert-deftest anqa-save-note-refuses-an-unrendered-note ()
  (with-temp-buffer
    (anqa-test--render)
    (let ((inhibit-read-only t))
      (goto-char (point-max))
      (insert "\n*** Fabricated\n:PROPERTIES:\n:ANQA_NOTE_ID: n-typed\n:END:\n\n"
              "**** Summary\n:PROPERTIES:\n:ANQA_FIELD_ID: summary\n:END:\n: typed\n"))
    (let (requests)
      (cl-letf (((symbol-function 'anqa-connect) (lambda () 'connection))
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection method &rest _keys)
                   (push method requests)
                   '(:revision "rev-2"))))
        (goto-char (point-min))
        (search-forward ": typed")
        (should-error (anqa-save-note) :type 'user-error))
      (should-not requests))))


;;; Mutations

(ert-deftest anqa-new-note-uses-schema-and-current-turn ()
  (with-temp-buffer
    (anqa-session-mode)
    (anqa--apply-document anqa-test--document "session-emacs" "rev-1" "session-emacs")
    (goto-char (point-min))
    (search-forward "inspect this")
    (let (saved-note)
      (cl-letf (((symbol-function 'anqa-connect) (lambda () 'connection))
                ((symbol-function 'anqa--do-refresh) #'ignore)
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection method params &rest _keys)
                   (pcase method
                     ("notes/list"
                      '(:schema (:fields [(:id "summary") (:id "detail")])
                        :revision "rev-1"))
                     ("notes/upsert"
                      (setq saved-note (plist-get params :note))
                      '(:revision "rev-2"))))))
        (anqa-new-note))
      (should (= (plist-get saved-note :turnIndex) 0))
      (should (string-prefix-p "n-" (plist-get saved-note :id)))
      (should (equal (gethash "summary" (plist-get saved-note :fields)) ""))
      (should (equal (gethash "detail" (plist-get saved-note :fields)) "")))))

(ert-deftest anqa-delete-note-sends-revision-safe-request ()
  (with-temp-buffer
    (anqa-session-mode)
    (anqa--apply-document anqa-test--document "session-emacs" "rev-1" "session-emacs")
    (goto-char (point-min))
    (search-forward "Original detail")
    (let (sent-params)
      (cl-letf (((symbol-function 'anqa-connect) (lambda () 'connection))
                ((symbol-function 'anqa--do-refresh) #'ignore)
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection method params &rest _keys)
                   (should (equal method "notes/delete"))
                   (setq sent-params params)
                   '(:revision "rev-2"))))
        (anqa-delete-note t))
      (should (equal (plist-get sent-params :session) "session-emacs"))
      (should (equal (plist-get sent-params :expectedRevision) "rev-1"))
      (should (equal (plist-get sent-params :noteId) "n-emacs")))))

(ert-deftest anqa-new-note-refuses-before-touching-the-server ()
  "A mutation the buffer cannot reload afterwards must not reach the server."
  (with-temp-buffer
    (anqa-test--render)
    (anqa-test--append ": First detail" " pending")
    (let (requests)
      (cl-letf (((symbol-function 'anqa-connect) (lambda () 'connection))
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection method &rest _keys)
                   (push method requests)
                   '(:revision "rev-2"))))
        (should-error (anqa-new-note) :type 'user-error))
      (should-not requests)
      (should (string-match-p "First detail pending" (buffer-string))))))

(ert-deftest anqa-delete-note-refuses-before-touching-the-server ()
  (with-temp-buffer
    (anqa-test--render)
    (anqa-test--append ": First detail" " pending")
    (let (requests)
      (cl-letf (((symbol-function 'anqa-connect) (lambda () 'connection))
                ((symbol-function 'jsonrpc-request)
                 (lambda (_connection method &rest _keys)
                   (push method requests)
                   '(:revision "rev-2"))))
        (should-error (anqa-delete-note t) :type 'user-error))
      (should-not requests))))


;;; Refresh and notifications

(ert-deftest anqa-refresh-keeps-flags-raised-during-the-request ()
  "A notification landing mid-render describes drift the response lacks."
  (with-temp-buffer
    (anqa-test--render)
    (cl-letf (((symbol-function 'anqa--render-session)
               (lambda (_session)
                 (setq anqa-notes-stale t)
                 (anqa-test--render-result))))
      (anqa--do-refresh))
    (should anqa-notes-stale)
    (should-not anqa-session-stale)))

(ert-deftest anqa-refresh-clears-flags-the-response-covers ()
  (with-temp-buffer
    (anqa-test--render)
    (setq anqa-notes-stale t
          anqa-session-stale t)
    (cl-letf (((symbol-function 'anqa--render-session)
               (lambda (_session) (anqa-test--render-result))))
      (anqa--do-refresh))
    (should-not anqa-notes-stale)
    (should-not anqa-session-stale)))

(ert-deftest anqa-auto-refresh-skips-buffers-with-unsaved-edits ()
  (with-temp-buffer
    (anqa-test--render)
    (anqa-test--append ": First detail" " pending")
    (let (refreshed)
      (cl-letf (((symbol-function 'anqa--do-refresh)
                 (lambda () (setq refreshed t))))
        (anqa--notification
         nil 'notes/changed '(:sessionId "session-emacs" :revision "rev-9")))
      (should-not refreshed)
      (should anqa-notes-stale)
      (should (buffer-modified-p))
      (should (string-match-p "First detail pending" (buffer-string))))))

(ert-deftest anqa-auto-refresh-reloads-a-clean-buffer ()
  (with-temp-buffer
    (anqa-test--render)
    (let (refreshed)
      (cl-letf (((symbol-function 'anqa--do-refresh)
                 (lambda () (setq refreshed t))))
        (anqa--notification
         nil 'notes/changed '(:sessionId "session-emacs" :revision "rev-9")))
      (should refreshed))))

(ert-deftest anqa-notifications-target-the-matching-session ()
  (let ((matching (generate-new-buffer " *anqa-matching*"))
        (other (generate-new-buffer " *anqa-other*")))
    (unwind-protect
        (cl-letf (((symbol-function 'anqa--do-refresh) #'ignore))
          (with-current-buffer matching
            (anqa-session-mode)
            (anqa--apply-document
             anqa-test--document "session-emacs" "rev-1" "session-emacs"))
          (with-current-buffer other
            (anqa-session-mode)
            (anqa--apply-document
             anqa-test--document "session-other" "rev-1" "session-other"))
          (anqa--notification
           nil 'notes/changed '(:sessionId "session-emacs" :revision "rev-2"))
          (anqa--notification
           nil 'session/changed '(:sessionId "session-emacs"))
          (with-current-buffer matching
            (should anqa-notes-stale)
            (should anqa-session-stale))
          (with-current-buffer other
            (should-not anqa-notes-stale)
            (should-not anqa-session-stale)))
      (kill-buffer matching)
      (kill-buffer other))))


;;; Opening sessions

(ert-deftest anqa-open-session-keeps-unsaved-edits-when-refused ()
  (let ((buffer (get-buffer-create "*anqa:session-emacs*"))
        asked opened)
    (unwind-protect
        (progn
          (with-current-buffer buffer
            (anqa-test--render)
            (anqa-test--append ": First detail" " pending"))
          (cl-letf (((symbol-function 'anqa--connection-for-session)
                     (lambda (_session) 'connection))
                    ((symbol-function 'pop-to-buffer) (lambda (target &rest _) target))
                    ((symbol-function 'yes-or-no-p)
                     (lambda (_prompt) (setq asked t) nil))
                    ((symbol-function 'jsonrpc-request)
                     (lambda (_connection method &rest _keys)
                       (pcase method
                         ("session/render" (anqa-test--render-result))
                         ("session/open" (setq opened t) nil)))))
            (anqa-open-session "session-emacs"))
          (should asked)
          (should opened)
          (with-current-buffer buffer
            (should (string-match-p "First detail pending" (buffer-string)))
            (should (buffer-modified-p))))
      (kill-buffer buffer))))

(ert-deftest anqa-open-session-re-renders-when-discard-is-confirmed ()
  (let ((buffer (get-buffer-create "*anqa:session-emacs*")))
    (unwind-protect
        (progn
          (with-current-buffer buffer
            (anqa-test--render)
            (anqa-test--append ": First detail" " pending"))
          (cl-letf (((symbol-function 'anqa--connection-for-session)
                     (lambda (_session) 'connection))
                    ((symbol-function 'pop-to-buffer) (lambda (target &rest _) target))
                    ((symbol-function 'yes-or-no-p) (lambda (_prompt) t))
                    ((symbol-function 'jsonrpc-request)
                     (lambda (_connection method &rest _keys)
                       (pcase method
                         ("session/render" (anqa-test--render-result))
                         ("session/open" nil)))))
            (anqa-open-session "session-emacs"))
          (with-current-buffer buffer
            (should-not (string-match-p "First detail pending" (buffer-string)))
            (should-not (buffer-modified-p))))
      (kill-buffer buffer))))

(ert-deftest anqa-session-entry-annotation-includes-status-and-model ()
  (let ((entry '(:sessionId "alpha-1"
                 :title "Socket review"
                 :status "complete"
                 :model "grok-4"
                 :harness "pi"
                 :harnessLabel "Pi")))
    (should (string-match-p "Socket review" (anqa--session-entry-annotation entry)))
    (should (string-match-p "complete" (anqa--session-entry-annotation entry)))
    (should (string-match-p "grok-4" (anqa--session-entry-annotation entry)))
    (should (string-match-p "Pi" (anqa--session-entry-annotation entry)))
    (should-not (string-match-p "host" (anqa--session-entry-annotation entry)))
    (should-not (string-match-p "work" (anqa--session-entry-annotation entry)))
    (should (equal (anqa--session-entry-path entry) "alpha-1"))))

(ert-deftest anqa-session-entry-path-prefers-path ()
  (should (equal (anqa--session-entry-path
                  '(:sessionId "alpha" :path "/tmp/alpha"))
                 "/tmp/alpha"))
  (should (equal (anqa--session-entry-path '(:sessionId "alpha"))
                 "alpha")))


;;; Connection lifecycle

(ert-deftest anqa-request-drops-a-dead-connection ()
  (let ((anqa--connection 'connection))
    (cl-letf (((symbol-function 'jsonrpc-running-p) (lambda (_connection) nil))
              ((symbol-function 'jsonrpc-request)
               (lambda (&rest _) (error "Peer gone"))))
      (should-error (anqa--request 'connection "session/render" nil))
      (should-not anqa--connection))))

(ert-deftest anqa-request-keeps-a-live-connection ()
  (let ((anqa--connection 'connection))
    (cl-letf (((symbol-function 'jsonrpc-running-p) (lambda (_connection) t))
              ((symbol-function 'jsonrpc-request)
               (lambda (&rest _) (error "Request timed out"))))
      (should-error (anqa--request 'connection "session/render" nil))
      (should (eq anqa--connection 'connection)))))

(ert-deftest anqa-killing-the-last-session-buffer-drops-the-connection ()
  (let ((first (generate-new-buffer " *anqa-one*"))
        (second (generate-new-buffer " *anqa-two*"))
        (anqa--connection 'connection)
        (shutdowns 0))
    (cl-letf (((symbol-function 'jsonrpc-shutdown)
               (lambda (_connection) (setq shutdowns (1+ shutdowns)))))
      (with-current-buffer first (anqa-session-mode))
      (with-current-buffer second (anqa-session-mode))
      (kill-buffer first)
      (should (eq anqa--connection 'connection))
      (should (= shutdowns 0))
      (kill-buffer second)
      (should-not anqa--connection)
      (should (= shutdowns 1)))))

(ert-deftest anqa-connection-for-session-restarts-a-stale-socket ()
  "A socket file outliving its TUI must not block reconnection forever."
  (let* ((directory (make-temp-file "anqa-session-" t))
         (socket (expand-file-name "control.sock" directory))
         (anqa--connection nil)
         (starts 0)
         (attempts 0))
    (unwind-protect
        (cl-letf (((symbol-function 'anqa-connected-p) (lambda () nil))
                  ((symbol-function 'anqa--socket-path) (lambda () socket))
                  ((symbol-function 'anqa-start)
                   (lambda (&rest _) (setq starts (1+ starts))))
                  ((symbol-function 'anqa-connect)
                   (lambda ()
                     (setq attempts (1+ attempts))
                     (when (= attempts 1)
                       (signal 'file-error
                               '("make client process failed" "Connection refused")))
                     'connection)))
          (write-region "" nil socket nil 'silent)
          (anqa--connection-for-session directory)
          (should (= starts 1))
          (should (= attempts 2)))
      (delete-directory directory t))))

(ert-deftest anqa-start-runs-anqad-detached ()
  "Missing socket starts anqad -d, not a TUI term."
  (let ((anqa-daemon-executable "anqad")
        started)
    (cl-letf (((symbol-function 'anqa--socket-path)
               (lambda () "/tmp/anqa-test.sock"))
              ((symbol-function 'start-process)
               (lambda (name _buffer program &rest args)
                 (setq started (cons program args))
                 (list 'fake-process name)))
              ((symbol-function 'set-process-query-on-exit-flag) #'ignore)
              ((symbol-function 'anqa--wait-for-socket) #'ignore))
      (anqa-start "/tmp/some-session" 3)
      (should (equal started '("anqad" "-d" "-s" "/tmp/anqa-test.sock"))))))

(ert-deftest anqa-session-list-all-retries-empty-building ()
  (let ((calls 0))
    (cl-letf (((symbol-function 'sit-for) (lambda (&rest _) t))
              ((symbol-function 'anqa--session-list)
               (lambda (&rest _)
                 (setq calls (1+ calls))
                 (if (< calls 2)
                     '(:sessions nil :matched 0 :total 0 :building t)
                   '(:sessions ((:sessionId "alpha" :path "grok:alpha"))
                     :matched 1 :total 1 :building :json-false)))))
      (let ((result (anqa--session-list-all "")))
        (should (>= calls 2))
        (should (equal (plist-get (car (plist-get result :sessions)) :sessionId)
                       "alpha"))))))

(ert-deftest anqa-sessions-buffer-refreshes-on-session-changed ()
  (let ((anqa--sessions-query "harness:grok")
        (buf (get-buffer-create "*anqa-sessions*")))
    (unwind-protect
        (cl-letf (((symbol-function 'anqa--session-list-all)
                   (lambda (query)
                     (should (equal query "harness:grok"))
                     '(:sessions ((:sessionId "g1" :path "grok:g1" :title "Grok one"
                                   :status "complete" :harnessLabel "Grok Build"))
                       :matched 1 :total 1))))
          (with-current-buffer buf
            (setq anqa--sessions-query "harness:grok"))
          (anqa--notification nil "session/changed" '(:sessionId ""))
          (with-current-buffer buf
            (should (string-match-p "Grok one" (buffer-string)))
            (should (string-match-p "matched 1 / total 1" (buffer-string)))))
      (kill-buffer buf))))

(ert-deftest anqa-find-session-sends-minibuffer-as-query ()
  (let (seen)
    (cl-letf (((symbol-function 'anqa--session-list)
               (lambda (query &rest _)
                 (push query seen)
                 '(:sessions ((:sessionId "g1" :path "grok:g1" :title "Grok one"
                               :status "complete" :harnessLabel "Grok Build"))
                   :matched 1 :total 1))))
      (anqa--session-completion-candidates "harness:grok")
      (should (member "harness:grok" seen))
      (should (gethash "complete  ·  Grok Build  ·  Grok one  ·  g1" anqa--find-table)))))

(ert-deftest anqa-render-session-omits-bodies-by-default ()
  (let (payload)
    (cl-letf (((symbol-function 'anqa--connection-for-session) (lambda (_) 'conn))
              ((symbol-function 'anqa--request)
               (lambda (_conn _method params)
                 (setq payload params)
                 '(:text "" :sessionId "s" :notesRevision "r"))))
      (anqa--render-session "grok:s")
      (should (eq (plist-get payload :bodies) :json-false))
      (anqa--render-session "grok:s" t 4)
      (should (eq (plist-get payload :bodies) t))
      (should (= (plist-get payload :promptIndex) 4)))))

(ert-deftest anqa-extract-prompt-section-is-one-headline ()
  (let ((text "* Session\n\n* Prompt 4\nbody-a\n\n* Prompt 9\nbody-b\n"))
    (should (string-prefix-p "* Prompt 4\nbody-a" (anqa--extract-prompt-section text 4)))
    (should-not (string-match-p "Prompt 9" (anqa--extract-prompt-section text 4)))))

(ert-deftest anqa-evil-binding-survives-byte-compilation ()
  "The Evil binding must call a function; a macro breaks in compiled code."
  (let* ((source (concat (file-name-sans-extension (locate-library "anqa")) ".el"))
         (compiled (make-temp-file "anqa-compiled" nil ".elc"))
         (byte-compile-dest-file-function (lambda (_source) compiled))
         (byte-compile-warnings nil)
         bindings)
    (unwind-protect
        (progn
          (should (byte-compile-file source))
          (cl-letf (((symbol-function 'evil-define-key)
                     (cons 'macro
                           (lambda (&rest _)
                             (error "Macro form reached at run time"))))
                    ((symbol-function 'evil-define-key*)
                     (lambda (state _keymap key definition)
                       (push (list state key definition) bindings))))
            (load compiled nil t)
            (provide 'evil))
          (should (cl-find #'anqa-refresh bindings :key #'cl-third)))
      (delete-file compiled))))

(provide 'anqa-tests)
;;; anqa-tests.el ends here
