import { Loader2, Send } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useSubmitTask } from "@/core/living-agents/hooks";

import { validateSubmitForm } from "./task-helpers";

export function TaskSubmitForm() {
  const [capability, setCapability] = useState("");
  const [description, setDescription] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const submitTask = useSubmitTask();

  const handleSubmit = () => {
    const error = validateSubmitForm(capability, description);
    if (error) {
      setFormError(error);
      return;
    }
    setFormError(null);
    submitTask.mutate(
      { capability: capability.trim(), description: description.trim() },
      {
        onSuccess: () => {
          setCapability("");
          setDescription("");
        },
      },
    );
  };

  const canSubmit =
    capability.trim().length > 0 &&
    description.trim().length > 0 &&
    !submitTask.isPending;

  return (
    <div className="rounded-lg border p-4">
      <div className="mb-3 flex items-center gap-2">
        <Send className="text-muted-foreground size-4" />
        <span className="font-medium">Submit New Task</span>
      </div>
      <div className="flex flex-col gap-3">
        <Input
          placeholder="Capability (e.g., dev, ops, research)"
          value={capability}
          onChange={(e) => setCapability(e.target.value)}
          disabled={submitTask.isPending}
        />
        <Textarea
          placeholder="Task description"
          rows={3}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          disabled={submitTask.isPending}
        />
        {formError && (
          <div className="text-destructive text-sm">{formError}</div>
        )}
        <Button
          onClick={handleSubmit}
          disabled={!canSubmit}
          className="self-start"
        >
          {submitTask.isPending ? (
            <>
              <Loader2 className="mr-1 size-4 animate-spin" />
              Submitting...
            </>
          ) : (
            "Submit"
          )}
        </Button>
      </div>
    </div>
  );
}
