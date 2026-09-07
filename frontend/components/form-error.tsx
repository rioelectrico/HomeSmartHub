type FormErrorProps = {
  id: string;
  message?: string;
};

export function FormError({ id, message }: FormErrorProps) {
  if (!message) return null;
  return (
    <p id={id} className="form-error" role="alert">
      {message}
    </p>
  );
}
