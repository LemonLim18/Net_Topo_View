import React, { createContext, useContext, useState, useCallback } from 'react';

export type TaskType = 'ai' | 'check' | 'sync' | 'capture' | 'refresh' | 'default';

export interface TaskItem {
  id: string;
  type: TaskType;
  message: string;
  subtitle?: string;
  status: 'processing' | 'done' | 'failed';
  minimized: boolean;
  checked: boolean;
  progressText?: string;
  created_at: number;
}

interface TaskProgressContextType {
  tasks: TaskItem[];
  addTask: (task: Omit<TaskItem, 'checked' | 'created_at'>) => void;
  updateTask: (id: string, updates: Partial<TaskItem>) => void;
  removeTask: (id: string) => void;
  minimizeTask: (id: string) => void;
  checkTask: (id: string) => void;
  clearCheckedTasks: () => void;
}

const TaskProgressContext = createContext<TaskProgressContextType | undefined>(undefined);

export const TaskProgressProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [tasks, setTasks] = useState<TaskItem[]>([]);

  const addTask = useCallback((task: Omit<TaskItem, 'checked' | 'created_at'>) => {
    setTasks((prev) => {
      // Avoid duplicate registrations
      if (prev.some((t) => t.id === task.id)) return prev;
      return [...prev, { ...task, checked: false, created_at: Date.now() }];
    });
  }, []);

  const updateTask = useCallback((id: string, updates: Partial<TaskItem>) => {
    setTasks((prev) =>
      prev.map((t) => (t.id === id ? { ...t, ...updates } : t))
    );
  }, []);

  const removeTask = useCallback((id: string) => {
    setTasks((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const minimizeTask = useCallback((id: string) => {
    setTasks((prev) =>
      prev.map((t) => (t.id === id ? { ...t, minimized: true } : t))
    );
  }, []);

  const checkTask = useCallback((id: string) => {
    setTasks((prev) =>
      prev.map((t) => (t.id === id ? { ...t, checked: true } : t))
    );
  }, []);

  const clearCheckedTasks = useCallback(() => {
    setTasks((prev) => prev.filter((t) => !t.checked));
  }, []);

  return (
    <TaskProgressContext.Provider
      value={{
        tasks,
        addTask,
        updateTask,
        removeTask,
        minimizeTask,
        checkTask,
        clearCheckedTasks,
      }}
    >
      {children}
    </TaskProgressContext.Provider>
  );
};

export const useTaskProgress = () => {
  const ctx = useContext(TaskProgressContext);
  if (!ctx) {
    throw new Error('useTaskProgress must be used within a TaskProgressProvider');
  }
  return ctx;
};
