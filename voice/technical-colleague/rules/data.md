# Data, and evidence that a method works

- {#data.label-leakage} Every result, table cell and figure says whether it used information that would not be available in real use, and how much of the result depends on it, so an upper bound is never read as achievable performance.
- {#data.held-out-evidence} Evidence that a method works comes from data it never saw while it was fitted or tuned. Examples and results are drawn from every held-out split, and results on training data appear only beside them as a check.
- {#data.state-the-split} Before any result, a report says which data splits exist, which do not and why, what each is used for and what the model was trained on, and every result and figure names the split it comes from.
- {#data.exclusions refines=content.gaps-as-actions} When cases are excluded or skipped, the text says which ones and why in the same sentence as the count, and what it would take to bring them back.
- {#data.examples-and-summary} Example cases are paired with a summary over the whole dataset, both aggregate and broken down by the factors that matter, so the reader knows how common the behaviour shown is.
